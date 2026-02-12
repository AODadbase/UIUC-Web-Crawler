#include <iostream>
#include <fstream>
#include <vector>
#include <string>
#include <thread>
#include <mutex>
#include <unordered_map>
#include <sstream>
#include <bitset>
#include <iomanip>

typedef uint64_t hash64;

// Utility: 64-bit FNV-1a hash function
hash64 fnv1a_hash(const std::string& str) {
    hash64 hash = 0xcbf29ce484222325;
    for (char c : str) {
        hash ^= (hash64)c;
        hash *= 0x100000001b3;
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
            else v[i]--;
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
    return std::bitset<64>(h1 ^ h2).count();
}

// Utility: extract a JSON string value, handling escaped quotes
std::string extract_value(const std::string& json, const std::string& key) {
    std::string search = "\"" + key + "\": \"";
    size_t start = json.find(search);
    if (start == std::string::npos) return "";
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

// Parallel worker: compute SimHash fingerprints for a chunk of lines
struct DocFingerprint {
    std::string url;
    hash64 fingerprint;
};

void simhash_worker(const std::vector<std::string>& lines, std::vector<DocFingerprint>& results, std::mutex& mtx) {
    std::vector<DocFingerprint> local_data;
    for (const auto& line : lines) {
        std::string url = extract_value(line, "url");
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
    int num_threads = std::max(1u, std::thread::hardware_concurrency());
    std::vector<std::thread> threads;
    std::vector<DocFingerprint> fingerprints;
    std::mutex mtx;

    std::vector<std::vector<std::string>> chunks(num_threads);
    for (size_t i = 0; i < all_lines.size(); ++i) chunks[i % num_threads].push_back(all_lines[i]);

    std::cout << "Calculating SimHash fingerprints using " << num_threads << " threads..." << std::endl;
    for (int i = 0; i < num_threads; ++i) {
        threads.emplace_back(simhash_worker, std::ref(chunks[i]), std::ref(fingerprints), std::ref(mtx));
    }
    for (auto& t : threads) t.join();

    // 2. Pairwise similarity comparison
    // Note: O(N^2) — for large datasets, use bucketed indexing
    std::cout << "Detecting near-duplicates (Hamming Distance <= 3)..." << std::endl;
    int duplicate_pairs = 0;

    // Save results to file
    std::ofstream out("simhash_results.txt");

    for (size_t i = 0; i < fingerprints.size(); ++i) {
        for (size_t j = i + 1; j < fingerprints.size(); ++j) {
            int dist = hamming_distance(fingerprints[i].fingerprint, fingerprints[j].fingerprint);
            if (dist <= 3) {
                std::cout << "  Potential Duplicate (Dist=" << dist << "):" << std::endl;
                std::cout << "   A: " << fingerprints[i].url << std::endl;
                std::cout << "   B: " << fingerprints[j].url << std::endl;
                if (out.is_open()) {
                    out << "dist=" << dist
                        << "\t" << fingerprints[i].url
                        << "\t" << fingerprints[j].url << "\n";
                }
                duplicate_pairs++;
            }
        }
    }

    if (out.is_open()) out.close();

    std::cout << "\nScan complete. Found " << duplicate_pairs << " near-duplicate pairs." << std::endl;
    std::cout << "Results saved to simhash_results.txt" << std::endl;
    return 0;
}
