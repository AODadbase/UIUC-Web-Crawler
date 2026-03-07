#include <iostream>
#include <fstream>
#include <vector>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <thread>
#include <mutex>
#include <cmath>
#include <iomanip>
#include <algorithm>

using Graph   = std::unordered_map<std::string, std::vector<std::string>>;
using RankMap = std::unordered_map<std::string, double>;

std::mutex graph_mutex;

// Utility: extract a JSON array of links.
// Handles both "links": [ and "links":[ (with or without space after colon).
std::vector<std::string> extract_links_from_json(const std::string& json) {
    std::vector<std::string> links;

    // Try both whitespace variants produced by different JSON serializers.
    const std::string key_spaced = "\"links\": [";
    const std::string key_tight  = "\"links\":[";

    size_t pos = json.find(key_spaced);
    size_t key_len = key_spaced.length();
    if (pos == std::string::npos) {
        pos = json.find(key_tight);
        key_len = key_tight.length();
    }
    if (pos == std::string::npos) return links;

    pos += key_len;
    size_t end = json.find("]", pos);
    if (end == std::string::npos) {
        // Malformed JSON; return no links rather than crashing.
        return links;
    }

    std::string content = json.substr(pos, end - pos);

    // Simple comma-delimited parsing for quoted strings.
    size_t start = 0;
    while ((pos = content.find("\"", start)) != std::string::npos) {
        size_t link_end = content.find("\"", pos + 1);
        if (link_end == std::string::npos) break;
        links.push_back(content.substr(pos + 1, link_end - pos - 1));
        start = link_end + 1;
    }
    return links;
}

// Utility: extract a JSON string value, handling escaped quotes.
std::string extract_value(const std::string& json, const std::string& key) {
    std::string search = "\"" + key + "\": \"";
    size_t start = json.find(search);
    if (start == std::string::npos) {
        // Also try tight variant ("key":"value")
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

// Phase 1: Build graph in parallel.
void build_graph_worker(const std::vector<std::string>& lines, Graph& graph, RankMap& ranks) {
    Graph   local_graph;
    RankMap local_nodes;

    for (const auto& line : lines) {
        std::string url = extract_value(line, "url");
        if (url.empty()) continue;

        std::vector<std::string> out_links = extract_links_from_json(line);

        // Merge into local graph: if two lines share a URL, combine their links
        // rather than overwriting the earlier entry.
        auto& existing = local_graph[url];
        existing.insert(existing.end(), out_links.begin(), out_links.end());
        local_nodes[url] = 1.0;

        for (const auto& link : out_links) {
            if (local_nodes.find(link) == local_nodes.end())
                local_nodes[link] = 1.0;
        }
    }

    // Critical section: merge into global graph.
    std::lock_guard<std::mutex> lock(graph_mutex);
    for (const auto& [u, links] : local_graph) {
        auto& g = graph[u];
        g.insert(g.end(), links.begin(), links.end());
    }
    for (const auto& [u, r] : local_nodes) {
        if (ranks.find(u) == ranks.end()) ranks[u] = 1.0;
    }
}

int main() {
    // 1. Read data
    std::ifstream file("raw_crawl.jsonl");
    std::vector<std::string> all_lines;
    std::string line;
    while (std::getline(file, line)) all_lines.push_back(line);

    if (all_lines.empty()) {
        std::cerr << "No data found! Run python crawler first.\n";
        return 1;
    }

    // 2. Build graph in parallel (adjacency list)
    int num_threads = static_cast<int>(std::max(1u, std::thread::hardware_concurrency()));
    std::vector<std::thread> threads;
    std::vector<std::vector<std::string>> chunks(num_threads);
    for (size_t i = 0; i < all_lines.size(); ++i) chunks[i % num_threads].push_back(all_lines[i]);

    Graph   graph;
    RankMap ranks;

    std::cout << "Building graph in parallel..." << std::endl;
    for (int i = 0; i < num_threads; ++i)
        threads.emplace_back(build_graph_worker, std::ref(chunks[i]), std::ref(graph), std::ref(ranks));
    for (auto& t : threads) t.join();
    threads.clear();

    std::cout << "Graph built. Nodes: " << ranks.size() << std::endl;

    // 3. Build reverse graph (pull-based PageRank)
    std::unordered_map<std::string, std::vector<std::string>> incoming_links;
    for (const auto& [source, targets] : graph) {
        for (const auto& target : targets) {
            incoming_links[target].push_back(source);
        }
    }

    // 4. Identify dangling nodes (no outgoing links).
    //    Without this, their rank mass leaks out of the system each iteration,
    //    biasing scores of high-authority sink pages downward.
    std::unordered_set<std::string> dangling_set;
    for (const auto& [node, _] : ranks) {
        if (!graph.count(node) || graph.at(node).empty()) {
            dangling_set.insert(node);
        }
    }
    std::cout << "Dangling nodes (sinks): " << dangling_set.size() << std::endl;

    // 5. PageRank iteration (BSP supersteps)
    int    max_iterations = 20;
    double damping        = 0.85;
    double epsilon        = 1e-6;
    size_t N              = ranks.size();

    std::vector<std::string> all_nodes;
    all_nodes.reserve(N);
    for (const auto& [u, r] : ranks) all_nodes.push_back(u);

    std::vector<std::vector<std::string>> node_chunks(num_threads);
    for (size_t i = 0; i < all_nodes.size(); ++i) node_chunks[i % num_threads].push_back(all_nodes[i]);

    std::cout << "Starting PageRank (max " << max_iterations
              << " iterations, epsilon=" << epsilon << ")..." << std::endl;

    for (int iter = 0; iter < max_iterations; ++iter) {
        // Compute dangling-node rank sum once per iteration (ranks is read-only during workers).
        double dangling_sum = 0.0;
        for (const auto& node : dangling_set) {
            dangling_sum += ranks.at(node);
        }
        // Each non-dangling, non-linking node redistributes its rank mass to all nodes equally.
        double dangling_contrib = damping * dangling_sum / static_cast<double>(N);
        double base_pr          = (1.0 - damping); // unnormalized teleportation term

        RankMap new_ranks;
        std::mutex new_ranks_mutex;

        auto worker = [&](const std::vector<std::string>& nodes_to_process) {
            // Use a thread-local accumulator to avoid per-node lock acquisition.
            RankMap local_new;
            local_new.reserve(nodes_to_process.size());

            for (const auto& node : nodes_to_process) {
                double sum = 0.0;
                if (incoming_links.count(node)) {
                    for (const auto& source : incoming_links.at(node)) {
                        size_t out_degree = graph.count(source) ? graph.at(source).size() : 0;
                        if (out_degree > 0) {
                            sum += ranks.at(source) / static_cast<double>(out_degree);
                        }
                    }
                }
                // Full formula: teleportation + dangling redistribution + link contribution.
                local_new[node] = base_pr + dangling_contrib + damping * sum;
            }

            std::lock_guard<std::mutex> lock(new_ranks_mutex);
            for (auto& [k, v] : local_new) new_ranks[k] = v;
        };

        for (int i = 0; i < num_threads; ++i)
            threads.emplace_back(worker, std::ref(node_chunks[i]));
        for (auto& t : threads) t.join();
        threads.clear();

        // Early convergence check
        double max_delta = 0.0;
        for (const auto& [node, new_rank] : new_ranks) {
            double old_rank = ranks.count(node) ? ranks.at(node) : 0.0;
            max_delta = std::max(max_delta, std::abs(new_rank - old_rank));
        }

        ranks = std::move(new_ranks);
        std::cout << "   Iteration " << iter + 1
                  << " complete (max delta: " << std::scientific << max_delta << ")." << std::endl;

        if (max_delta < epsilon) {
            std::cout << "   Converged after " << iter + 1 << " iterations." << std::endl;
            break;
        }
    }

    // 6. Sort and output top 10
    std::cout << "\nTop 10 Pages by Rank:\n";
    std::vector<std::pair<std::string, double>> sorted_ranks(ranks.begin(), ranks.end());
    std::sort(sorted_ranks.begin(), sorted_ranks.end(),
              [](const auto& a, const auto& b) { return a.second > b.second; });

    for (size_t i = 0; i < 10 && i < sorted_ranks.size(); ++i) {
        std::cout << i + 1 << ". [" << std::fixed << std::setprecision(4)
                  << sorted_ranks[i].second << "] " << sorted_ranks[i].first << std::endl;
    }

    // 7. Save all results
    std::ofstream out("pagerank_results.txt");
    if (out.is_open()) {
        for (const auto& [url, rank] : sorted_ranks) {
            out << std::fixed << std::setprecision(6) << rank << "\t" << url << "\n";
        }
        out.close();
        std::cout << "\nPageRank scores saved to pagerank_results.txt ("
                  << sorted_ranks.size() << " entries)" << std::endl;
        std::cout << "These scores will be injected as metadata by log_resolver." << std::endl;
    } else {
        std::cerr << "Warning: Could not write pagerank_results.txt" << std::endl;
    }

    return 0;
}
