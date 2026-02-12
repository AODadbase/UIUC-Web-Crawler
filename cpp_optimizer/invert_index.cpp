#include <iostream>
#include <fstream>
#include <string>
#include <vector>
#include <thread>
#include <mutex>
#include <unordered_map>
#include <unordered_set>
#include <sstream>
#include <algorithm>

using IndexMap = std::unordered_map<std::string, std::vector<std::string>>;

// Simple word tokenization and cleanup
std::string clean_word(std::string w) {
    std::string clean;
    for (char c : w) {
        if (std::isalnum(c)) clean += std::tolower(c);
    }
    return clean;
}

// Utility: extract a JSON string value, handling escaped quotes
std::string get_val(const std::string& json, const std::string& key) {
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

// Parallel index builder
void index_worker(const std::vector<std::string>& lines, IndexMap& global_index, std::mutex& mtx) {
    IndexMap local_index;
    for (const auto& line : lines) {
        std::string url = get_val(line, "url");
        std::string content = get_val(line, "content");

        std::stringstream ss(content);
        std::string word;
        while (ss >> word) {
            std::string cw = clean_word(word);
            if (cw.length() > 3) { // Skip very short words
                local_index[cw].push_back(url);
            }
        }
    }

    // Merge into global index (reduce phase)
    std::lock_guard<std::mutex> lock(mtx);
    for (auto& [word, urls] : local_index) {
        global_index[word].insert(global_index[word].end(), urls.begin(), urls.end());
    }
}

int main() {
    std::ifstream file("raw_crawl.jsonl");
    std::vector<std::string> lines;
    std::string line;
    while (std::getline(file, line)) lines.push_back(line);

    IndexMap global_index;
    std::mutex mtx;
    int num_threads = std::max(1u, std::thread::hardware_concurrency());
    std::vector<std::thread> threads;

    // Partition data across threads
    std::vector<std::vector<std::string>> chunks(num_threads);
    for(size_t i=0; i<lines.size(); ++i) chunks[i%num_threads].push_back(lines[i]);

    std::cout << "Building parallel inverted index..." << std::endl;
    for (int i=0; i<num_threads; ++i)
        threads.emplace_back(index_worker, std::ref(chunks[i]), std::ref(global_index), std::ref(mtx));
    for (auto& t : threads) t.join();

    std::cout << "Index built. Total unique words: " << global_index.size() << std::endl;

    // Save index to file, deduplicating URLs per word
    std::ofstream out("inverted_index.txt");
    if (out.is_open()) {
        for (const auto& [word, urls] : global_index) {
            // Deduplicate URLs using unordered_set
            std::unordered_set<std::string> unique_urls(urls.begin(), urls.end());
            out << word << "\t";
            bool first = true;
            for (const auto& url : unique_urls) {
                if (!first) out << " ";
                out << url;
                first = false;
            }
            out << "\n";
        }
        out.close();
        std::cout << "Index saved to inverted_index.txt" << std::endl;
    }

    // Example search
    std::string query = "computer";
    if (global_index.count(query)) {
        // Deduplicate for display too
        std::unordered_set<std::string> unique(global_index[query].begin(), global_index[query].end());
        std::cout << "Found '" << query << "' in " << unique.size() << " pages." << std::endl;
    }
    return 0;
}
