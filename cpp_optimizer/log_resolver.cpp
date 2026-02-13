#include <iostream>
#include <fstream>
#include <string>
#include <vector>
#include <thread>
#include <mutex>
#include <queue>
#include <condition_variable>
#include <filesystem>
#include <sstream>
#include <unordered_map>

namespace fs = std::filesystem;
#include <algorithm>

// Global PageRank scores map (loaded once at startup)
std::unordered_map<std::string, double> pagerank_scores;
std::mutex pr_mutex;

// Data structure for a crawled page entry
struct PageEntry {
    std::string url;
    std::string title;
    std::string content;
};

// Thread-safe queue for producer-consumer pattern
template <typename T>
class SafeQueue {
    std::queue<T> q;
    std::mutex m;
    std::condition_variable cv;
    bool done = false;
public:
    void push(T val) {
        std::lock_guard<std::mutex> lock(m);
        q.push(val);
        cv.notify_one();
    }
    bool pop(T& val) {
        std::unique_lock<std::mutex> lock(m);
        cv.wait(lock, [this]{ return !q.empty() || done; });
        if (q.empty() && done) return false;
        val = q.front();
        q.pop();
        return true;
    }
    void finish() {
        std::lock_guard<std::mutex> lock(m);
        done = true;
        cv.notify_all();
    }
};

// Utility: simple JSON value extractor for this project's standard JSON format
// Handles escaped quotes within values
std::string extract_json_value(const std::string& json, const std::string& key) {
    std::string search_key = "\"" + key + "\": \"";
    size_t start_pos = json.find(search_key);
    if (start_pos == std::string::npos) return "";

    start_pos += search_key.length();
    size_t end_pos = start_pos;

    // Find closing quote, skipping escaped characters
    bool escaped = false;
    while (end_pos < json.length()) {
        if (escaped) {
            escaped = false;
        } else {
            if (json[end_pos] == '\\') escaped = true;
            else if (json[end_pos] == '"') break;
        }
        end_pos++;
    }

    return json.substr(start_pos, end_pos - start_pos);
}

// Utility: sanitize a URL into a safe filename
std::string sanitize_filename(std::string url) {
    std::string filename = url;
    // Replace illegal characters
    std::string illegal = "://.";
    for (char c : illegal) {
        std::replace(filename.begin(), filename.end(), c, '_');
    }
    // Truncate length
    if (filename.length() > 100) filename = filename.substr(0, 100);
    return filename + ".md";
}

// Load PageRank scores from file
void load_pagerank_scores(const std::string& pr_file) {
    std::ifstream file(pr_file);
    if (!file.is_open()) {
        std::cerr << "Warning: PageRank file not found. Scores will default to 1.0" << std::endl;
        return;
    }
    
    double score;
    std::string url;
    int count = 0;
    
    while (file >> score) {
        std::getline(file, url); // Read rest of line (tab + url)
        // Trim leading whitespace/tab
        size_t start = url.find_first_not_of(" \t");
        if (start != std::string::npos) {
            url = url.substr(start);
            pagerank_scores[url] = score;
            count++;
        }
    }
    
    std::cout << "Loaded " << count << " PageRank scores from " << pr_file << std::endl;
}

// Consumer thread: generate Markdown files from parsed JSON lines
void file_writer_worker(SafeQueue<std::string>& input_queue, std::string base_dir) {
    std::string line;
    while (input_queue.pop(line)) {
        // 1. Parse fields
        std::string url = extract_json_value(line, "url");
        std::string title = extract_json_value(line, "title");
        std::string content = extract_json_value(line, "content");
        std::string category = extract_json_value(line, "category");
        if (category.empty()) category = "uncategorized";

        // 2. Ensure category subdirectory exists and generate filepath
        std::string category_dir = base_dir + "/" + category;
        fs::create_directories(category_dir);
        std::string filename = sanitize_filename(url);
        std::string filepath = category_dir + "/" + filename;

        // 3. Write Markdown (unescape \n, \t, \" in content)
        std::string clean_content;
        for (size_t i = 0; i < content.length(); ++i) {
            if (content[i] == '\\' && i + 1 < content.length()) {
                if (content[i+1] == 'n') { clean_content += '\n'; i++; }
                else if (content[i+1] == 't') { clean_content += '\t'; i++; }
                else if (content[i+1] == '"') { clean_content += '"'; i++; }
                else { clean_content += content[i]; }
            } else {
                clean_content += content[i];
            }
        }

        // Get PageRank score for this URL (default to 1.0 if not found)
        double pr_score = 1.0;
        {
            std::lock_guard<std::mutex> lock(pr_mutex);
            if (pagerank_scores.count(url)) {
                pr_score = pagerank_scores[url];
            }
        }

        // Determine priority based on PageRank threshold
        std::string priority = (pr_score > 5.0) ? "high" : "normal";

        std::ofstream outfile(filepath);
        if (outfile.is_open()) {
            // Write YAML frontmatter
            outfile << "---\n";
            outfile << "url: " << url << "\n";
            outfile << "title: " << title << "\n";
            outfile << "category: " << category << "\n";
            outfile << "pagerank_score: " << std::fixed << std::setprecision(6) << pr_score << "\n";
            outfile << "priority: " << priority << "\n";
            outfile << "---\n\n";

            // Write content
            outfile << "# " << title << "\n\n";
            outfile << clean_content;
            outfile.close();
        }
    }
}

int main() {
    std::string log_file = "raw_crawl.jsonl";
    std::string base_dir = "uiuc_knowledge_base";
    std::string pr_file = "pagerank_results.txt";

    // Load PageRank scores
    load_pagerank_scores(pr_file);

    // Ensure base output directory exists
    fs::create_directories(base_dir);

    SafeQueue<std::string> queue;

    // Use hardware concurrency for thread count
    int num_threads = std::max(1u, std::thread::hardware_concurrency());
    std::vector<std::thread> workers;
    for (int i = 0; i < num_threads; ++i) {
        workers.emplace_back(file_writer_worker, std::ref(queue), base_dir);
    }

    std::cout << "C++ Middleware starting..." << std::endl;
    std::cout << "Reading from " << log_file << " -> Writing to " << base_dir << std::endl;

    // Main thread: read lines and push into queue
    std::ifstream file(log_file);
    if (!file.is_open()) {
        std::cerr << "Could not open log file!" << std::endl;
        queue.finish();
        return 1;
    }

    std::string line;
    int count = 0;
    while (std::getline(file, line)) {
        queue.push(line);
        count++;
        if (count % 100 == 0) std::cout << "\rProcessing: " << count << " lines..." << std::flush;
    }

    std::cout << "\nAll lines read. Waiting for workers..." << std::endl;

    queue.finish();
    for (auto& t : workers) t.join();

    std::cout << "Done! All markdown files generated." << std::endl;
    return 0;
}
