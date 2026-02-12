CXX = g++
CXXFLAGS = -std=c++17 -O3 -Wall -pthread

TARGETS = log_resolver pagerank simhash inverted_index

all: $(TARGETS)

log_resolver: log_resolver.cpp
	$(CXX) $(CXXFLAGS) -o log_resolver log_resolver.cpp

pagerank: pagerank.cpp
	$(CXX) $(CXXFLAGS) -o pagerank pagerank.cpp

simhash: simhash.cpp
	$(CXX) $(CXXFLAGS) -o simhash simhash.cpp

inverted_index: invert_index.cpp
	$(CXX) $(CXXFLAGS) -o inverted_index invert_index.cpp

clean:
	rm -f $(TARGETS)
