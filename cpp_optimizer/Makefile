CXX = g++
CXXFLAGS = -std=c++17 -O3 -pthread

TARGET = log_resolver
SRC = log_resolver.cpp

all: $(TARGET)

$(TARGET): $(SRC)
	$(CXX) $(CXXFLAGS) -o $(TARGET) $(SRC)

clean:
	rm -f $(TARGET)