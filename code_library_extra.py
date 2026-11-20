"""Additional curated coding recipe combinations for CodeAI Ultimate."""
DOMAINS = [
    "text processing", "file management", "CSV data", "JSON data", "date and time",
    "mathematics", "statistics", "sorting", "searching", "arrays and lists",
    "strings", "dictionaries and maps", "sets", "queues and stacks", "linked lists",
    "trees", "graphs", "network requests", "REST APIs", "database access",
    "SQLite databases", "user authentication demos", "input validation", "logging",
    "error handling", "unit testing", "command-line tools", "desktop interfaces",
    "web interfaces", "image metadata", "configuration files"
]
TASKS = [
    "create a beginner example", "build a reusable utility", "write a validation function",
    "implement a search feature", "implement a sorting feature", "create a CRUD workflow",
    "add pagination", "add filtering", "add safe error handling", "add structured logging",
    "write unit tests", "read from a file", "write to a file", "convert between formats",
    "summarize records", "remove duplicate entries", "group records by a field",
    "calculate summary statistics", "design a small class-based example",
    "create a command-line interface", "create a menu-driven program",
    "process a batch of records", "handle missing values", "validate user input",
    "serialize and deserialize data", "add caching", "add configurable options",
    "create a modular package example", "document a public function",
    "demonstrate a safe, minimal implementation"
]
EXTRA_RECIPES = [f"{task.title()} for {domain}" for domain in DOMAINS for task in TASKS]
