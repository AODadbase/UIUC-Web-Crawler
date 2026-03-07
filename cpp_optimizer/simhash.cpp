#include <iostream>
#include <fstream>
#include <vector>
#include <string>
#include <thread>
#include <mutex>
#include <unordered_map>
#include <unordered_set>
#include <sstream>
#include <bitset>
#include <iomanip>

typedef uint64_t hash64;

// Utility: 64-bit FNV-1a hash function
hash64 fnv1a_hash(const std::string& str) {
    hash64 hash = 0xcbf29ce484222325ULL;
    for (char c : str) {
        hash ^= static_cast<hash64>(static_cast<unsigned char>(c));
        hash *= 0x100000001b3ULL;
    }
    return hash;
}

// Core algorithm: compute SimHash fingerprint for a document
hash64 calculate_simhash(const std::string& content) {
    std::vector<int> v(64, 0);
    std::stringstream ss(content);
    std::string word;

    while (ss >> word) {
        hash64 h = fnv1a_hash(word);
        for (int i = 0; i < 64; ++i) {
            if ((h >> i) & 1) v[i]++;
            else               v[i]--;
        }
    }

    hash64 fingerprint = 0;
    for (int i = 0; i < 64; ++i) {
        if (v[i] > 0) fingerprint |= (1ULL << i);
    }
    return fingerprint;
}

// Utility: compute Hamming distance between two 64-bit hashes
int hamming_distance(hash64 h1, hash64 h2) {
    return static_cast<int>(std::bitset<64>(h1 ^ h2).count());
}

// Utility: extract a JSON string value, handling escaped quotes
std::string extract_value(const std::string& json, const std::string& key) {
    std::string search = "\"" + key + "\": \"";
    size_t start = json.find(search);
    if (start == std::string::npos) {
        search = "\"" + key + "\":\"";
        start = json.find(search);
        if (start == std::string::npos) return "";
    }
    start += search.length();

    size_t end = start;
    bool escaped = false;
    while (end < json.length()) {
        if (escaped) {
            escaped = false;
        } else {
            if (json[end] == '\\') escaped = true;
            else if (json[end] == '"') break;
        }
        end++;
    }
    return json.substr(start, end - start);
}

struct DocFingerprint {
    std::string url;
    hash64      fingerprint;
};

// Parallel worker: compute SimHash fingerprints for a chunk of lines
void simhash_worker(const std::vector<std::string>& lines,
                    std::vector<DocFingerprint>& results,
                    std::mutex& mtx) {
    std::vector<DocFingerprint> local_data;
    for (const auto& line : lines) {
        std::string url     = extract_value(line, "url");
        std::string content = extract_value(line, "content");
        if (url.empty() || content.empty()) continue;
        local_data.push_back({url, calculate_simhash(content)});
    }
    std::lock_guard<std::mutex> lock(mtx);
    results.insert(results.end(), local_data.begin(), local_data.end());
}

int main() {
    std::string input_file = "raw_crawl.jsonl";
    std::ifstream file(input_file);
    std::vector<std::string> all_lines;
    std::string line;

    std::cout << "Loading data for SimHash..." << std::endl;
    while (std::getline(file, line)) all_lines.push_back(line);

    if (all_lines.empty()) return 1;

    // 1. Compute fingerprints in parallel (Map phase)
    int num_threads = static_cast<int>(std::max(1u, std::thread::hardware_concurrency()));
    std::vector<std::thread> threads;
    std::vector<DocFingerprint> fingerprints;
    std::mutex mtx;

    std::vector<std::vector<std::string>> chunks(num_threads);
    for (size_t i = 0; i < all_lines.size(); ++i) chunks[i % num_threads].push_back(all_lines[i]);

    std::cout << "Calculating SimHash fingerprints using " << num_threads << " threads..." << std::endl;
    for (int i = 0; i < num_threads; ++i)
        threads.emplace_back(simhash_worker, std::ref(chunks[i]), std::ref(fingerprints), std::ref(mtx));
    for (auto& t : threads) t.join();

    // 2. Block-based near-duplicate detection (replaces naive O(N²) scan).
    //
    // Pigeonhole guarantee: split the 64-bit fingerprint into 4 blocks of 16 bits.
    // If Hamming distance(a, b) <= 3, then at most 3 bits differ total.
    // By pigeonhole, at least one of the 4 blocks must be IDENTICAL between a and b.
    // So: group documents by each block value, then verify only intra-bucket pairs.
    // This produces zero false negatives for HD <= 3 and scales as O(N * avg_bucket_size)
    // rather than O(N²).
    //
    // For the current crawl size (~300 pages) the saving is modest, but this keeps
    // the code correct at larger scales without algorithmic changes.

    const int      NUM_BLOCKS = 4;
    const int      BLOCK_BITS = 16;   // 4 * 16 = 64
    const hash64   BLOCK_MASK = (1ULL << BLOCK_BITS) - 1;

    // buckets[b][block_value] = list of document indices whose b-th block equals block_value
    std::vector<std::unordered_map<hash64, std::vector<size_t>>> buckets(NUM_BLOCKS);
    for (size_t i = 0; i < fingerprints.size(); ++i) {
        for (int b = 0; b < NUM_BLOCKS; ++b) {
            hash64 block_val = (fingerprints[i].fingerprint >> (b * BLOCK_BITS)) & BLOCK_MASK;
            buckets[b][block_val].push_back(i);
        }
    }

    std::cout << "Detecting near-duplicates (Hamming Distance <= 3, block-based)..." << std::endl;
    int duplicate_pairs = 0;

    std::ofstream out("simhash_results.txt");

    // checked_pairs encodes (i, j) as a single uint64 to avoid re-checking pairs
    // found via multiple matching blocks.
    std::unordered_set<hash64> checked_pairs;

    for (int b = 0; b < NUM_BLOCKS; ++b) {
        for (auto& [bval, indices] : buckets[b]) {
            for (size_t xi = 0; xi < indices.size(); ++xi) {
                for (size_t xj = xi + 1; xj < indices.size(); ++xj) {
                    size_t i = indices[xi];
                    size_t j = indices[xj];
                    if (i > j) std::swap(i, j);

                    // Encode pair as single 64-bit key (safe for N <= 2^32)
                    hash64 pair_key = (static_cast<hash64>(i) << 32) | static_cast<hash64>(j);
                    if (!checked_pairs.insert(pair_key).second) continue;

                    int dist = hamming_distance(fingerprints[i].fingerprint,
                                               fingerprints[j].fingerprint);
                    if (dist <= 3) {
                        std::cout << "  Potential Duplicate (Dist=" << dist << "):\n";
                        std::cout << "   A: " << fingerprints[i].url << "\n";
                        std::cout << "   B: " << fingerprints[j].url << "\n";
                        if (out.is_open()) {
                            out << "dist=" << dist
                                << "\t" << fingerprints[i].url
                                << "\t" << fingerprints[j].url << "\n";
                        }
                        duplicate_pairs++;
                    }
                }
            }
        }
    }

    if (out.is_open()) out.close();

    std::cout << "\nScan complete. Found " << duplicate_pairs << " near-duplicate pairs." << std::endl;
    std::cout << "Results saved to simhash_results.txt" << std::endl;
    return 0;
}
