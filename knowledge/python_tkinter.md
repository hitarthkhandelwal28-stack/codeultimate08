# Python desktop GUI and Tkinter engineering reference

Use this reference when building, reviewing, or debugging Python desktop applications, especially calculators.

## Tkinter structure
- Use `tk.Tk` for the application root and `ttk` widgets for a consistent native interface.
- Keep UI construction, event callbacks, and calculation logic in separate methods/functions.
- Use `grid`, `pack`, or `place` consistently within a given parent frame; do not mix geometry managers in the same parent.
- For button callbacks in loops, capture the current value with a default argument, e.g. `command=lambda value=label: self.on_click(value)`.
- Keep the UI responsive; do not perform long network or CPU-heavy work on the Tkinter main thread.
- Support keyboard shortcuts where appropriate (Enter to calculate, Backspace to delete, Escape to clear).
- Give labels and buttons accessible, meaningful names and ensure the window behaves at small screen sizes.

## Calculator input safety
- Do not use Python `eval()` or `exec()` on user-entered expressions, even after checking allowed characters. Character allowlists alone do not enforce valid arithmetic syntax or resource limits.
- Prefer a small parser based on Python's `ast.parse(expression, mode="eval")`. Explicitly allow only numeric constants, unary plus/minus, and selected binary operators such as addition, subtraction, multiplication, and division.
- Reject names, function calls, attributes, indexing, comprehensions, booleans, strings, and every AST node not explicitly allowed.
- Enforce expression length and numeric magnitude limits. Consider rejecting non-finite results with `math.isfinite`.
- Handle `ZeroDivisionError`, malformed syntax, unsupported operators, and overflow with short user-friendly messages. Do not display internal tracebacks to end users.
- Avoid exponentiation unless it is deliberately supported with strict exponent and result limits.

## Validation and state
- Validate input before calculation and keep the original expression visible when an error occurs.
- Define clear behavior for empty input, repeated equals, decimal points, consecutive operators, and backspace.
- Separate the calculation function from Tkinter so it can be unit-tested without opening a GUI.
- Prefer returning a numeric result or raising a specific exception from the calculation layer; format it for display in the UI layer.
- Avoid silently converting invalid expressions into zero.

## Testing checklist
- Test normal precedence: `2 + 3 * 4`.
- Test parentheses: `(2 + 3) * 4`.
- Test negative values and decimals.
- Test division by zero and malformed expressions.
- Test rejection of names, calls, attributes, strings, and unsupported operators.
- Test button callback behavior and keyboard bindings separately from the arithmetic parser.
- Use pytest for calculation logic; GUI tests should avoid requiring a visible display where possible.

## Review response format
When reviewing a small Tkinter calculator, report:
1. What is already implemented well.
2. Correctness and security issues.
3. A prioritized list of improvements.
4. Complete revised code only when requested.
5. Tests and exact commands to run them.
