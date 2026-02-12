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

namespace fs = std::filesystem;

// 定义数据结构
struct PageEntry {
    std::string url;
    std::string title;
    std::string content;
};

// 简单的线程安全队列
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

// 工具函数：手动解析简单 JSON (提取 content, url, title)
// 注意：这只是针对本项目生成的标准 JSON 的简化解析器
std::string extract_json_value(const std::string& json, const std::string& key) {
    std::string search_key = "\"" + key + "\": \"";
    size_t start_pos = json.find(search_key);
    if (start_pos == std::string::npos) return "";
    
    start_pos += search_key.length();
    size_t end_pos = start_pos;
    
    // 简单的查找结尾引号，处理转义字符略过
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

// 工具函数：清理文件名
std::string sanitize_filename(std::string url) {
    std::string filename = url;
    // 替换非法字符
    std::string illegal = "://."; 
    for (char c : illegal) {
        std::replace(filename.begin(), filename.end(), c, '_');
    }
    // 截断长度
    if (filename.length() > 100) filename = filename.substr(0, 100);
    return filename + ".md";
}

// 消费者线程：生成 Markdown 文件
void file_writer_worker(SafeQueue<std::string>& input_queue, std::string output_dir) {
    std::string line;
    while (input_queue.pop(line)) {
        // 1. 解析
        std::string url = extract_json_value(line, "url");
        std::string title = extract_json_value(line, "title");
        std::string content = extract_json_value(line, "content"); // 实际上内容里的转义字符需要处理，这里简化

        if (url.empty() || content.empty()) continue;

        // 2. 生成文件名
        std::string filename = sanitize_filename(url);
        std::string filepath = output_dir + "/" + filename;

        // 3. 写入 Markdown (还原 content 中的转义换行)
        // 简单的反转义处理：将 \n 换回 换行符
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

        std::ofstream outfile(filepath);
        if (outfile.is_open()) {
            outfile << "> URL: " << url << "\n";
            outfile << "> Title: " << title << "\n";
            outfile << "> Category: uncategorized\n\n";
            outfile << "# " << title << "\n\n";
            outfile << clean_content;
            outfile.close();
        }
    }
}

int main() {
    std::string log_file = "raw_crawl.jsonl";
    std::string output_dir = "uiuc_knowledge_base/uncategorized";

    // 确保输出目录存在
    fs::create_directories(output_dir);

    SafeQueue<std::string> queue;
    
    // 启动 4 个写入线程 (并发处理 IO)
    int num_threads = 4;
    std::vector<std::thread> workers;
    for (int i = 0; i < num_threads; ++i) {
        workers.emplace_back(file_writer_worker, std::ref(queue), output_dir);
    }

    std::cout << "🚀 C++ Middleware starting..." << std::endl;
    std::cout << "📂 Reading from " << log_file << " -> Writing to " << output_dir << std::endl;

    // 主线程：读取文件并推入队列
    std::ifstream file(log_file);
    if (!file.is_open()) {
        std::cerr << "❌ Could not open log file!" << std::endl;
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
    
    std::cout << "\n✅ All lines read. Waiting for workers..." << std::endl;

    queue.finish();
    for (auto& t : workers) t.join();

    std::cout << "🎉 Done! All markdown files generated." << std::endl;
    return 0;
}