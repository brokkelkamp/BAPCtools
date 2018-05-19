// A header library to safely parse team input.
// It does not support floating points or big integers.

// The easiest way to use this is to symlink it from a validator directory,
// so that it will be picked up when creating a contest zip.

// The default checking behaviour is lenient for both white space and case.
// When validating .in and .ans files, the case_sensitve and space_change_sensitive flags should be
// passed. When validating team output, the flags in problem.yaml should be used.

#include <algorithm>
#include <fstream>
#include <iostream>
using namespace std;

const string case_sensitive_flag         = "case_sensitive";
const string space_change_sensitive_flag = "space_change_sensitive";

class Validator {
	const int ret_AC = 42, ret_WA = 43;
	bool case_sensitive;
	bool ws;

  public:
	Validator(int argc, char **argv, istream &in = std::cin) : in(in) {
		for(int i = 0; i < argc; ++i) {
			if(argv[i] == case_sensitive_flag) case_sensitive = true;
			if(argv[i] == space_change_sensitive_flag) ws = true;
		}
		if(ws) in >> noskipws;
	}

	// At the end of the scope, check whether the EOF has been reached.
	// If so, return AC. Otherwise, return WA.
	~Validator() {
		eof();
		AC();
	}

	void space() {
		if(ws) {
			char c;
			in >> c;
			if(c != ' ') expected("space", string("\"") + c + "\"");
		}
		cerr << "read space!\n";
	}

	void newline() {
		if(ws) {
			char c;
			in >> c;
			if(c != '\n') expected("newline", string("\"") + c + "\"");
		}
		cerr << "read newline!\n";
	}

	// Read an arbitrary string.
	// Argument is only used for error reporting.
	// Use text_string to read fixed string.
	string read_string(string wanted = "string") {
		if(ws) {
			char next = in.peek();
			if(isspace(next)) expected(wanted, "whitespace");
		}
		string s;
		if(in >> s) return s;
		expected(wanted, "nothing");
	}

	// Read an arbitrary string of a given length.
	string read_string(size_t min, size_t max) {
		string s = read_string();
		if(s.size() < min || s.size() > max)
			expected("String of length between " + to_string(min) + " and " + to_string(max), s);
		return s;
	}

	// Read the string t.
	void test_string(string t) {
		string s = read_string();
		if(lowercase(s) != lowercase(t)) expected(t, s);
	}

	// Check whether a string is an integer.
	void is_int(const string &s) {
		auto it = s.begin();
		// [0-9-]
		if(!(*it == '-' || ('0' <= *it && *it <= '9')))
			expected("integer with leading digit or minus sign", s);
		++it;
		for(; it != s.end(); ++it)
			if(!('0' <= *it && *it <= '9')) expected("integer", s);
	}

	// Read a long long.
	long long read_long_long() {
		string s = read_string("integer");
		is_int(s);
		long long v;
		try {
			v = stoll(s);
		} catch(const out_of_range &e) {
			WA("Number " + s + " does not fit in a long long!");
		} catch(const invalid_argument &e) { WA("Parsing " + s + " as long long failed!"); }
		return v;
	}

	// Read a long long within a given range.
	long long read_long_long(long long low, long long high) {
		auto v = read_long_long();
		if(low <= v && v <= high) return v;
		expected("integer between " + to_string(low) + " and " + to_string(high), to_string(v));
		return v;
	}

	// Check the next character.
	bool peek(char c) {
		if(!ws) in >> ::ws;
		return in.peek() == char_traits<char>::to_int_type(c);
	}

	// Return WRONG ANSWER verdict.
	[[noreturn]] void expected(string exp = "", string s = "") {
		if(s.size())
			cout << "Expected " << exp << ", found " << s << endl;
		else if(exp.size())
			cout << exp << endl;
		exit(ret_WA);
	}

	template <typename... T>
	[[noreturn]] void WA(T... t) {
		(cout << ... << t) << endl;
		exit(ret_WA);
	}

	private :
	    // Return ACCEPTED verdict.
	    [[noreturn]] void AC() { exit(ret_AC); }

	    void eof() {
		if(in.eof()) return;
		// Sometimes EOF hasn't been triggered yet.
		if(!ws) in >> ::ws;
		char c = in.get();
		if(c == char_traits<char>::eof()) return;
		expected("EOF", string("\"") + char(c) + "\"");
	}

	// Convert a string to lowercase is matching is not case sensitive.
	string &lowercase(string &s) {
		if(!case_sensitive) return s;
		transform(s.begin(), s.end(), s.begin(), ::tolower);
		return s;
	}

	istream &in;
};
