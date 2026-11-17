import ai_engine


def test_tkinter_questions_receive_python_gui_reference(monkeypatch):
    captured = {}

    def fake_request(messages, **kwargs):
        captured["messages"] = messages
        return "review complete"

    monkeypatch.setattr(ai_engine, "_request", fake_request)
    answer = ai_engine.ask_codeai([
        {"role": "user", "content": "Review my Tkinter calculator code"}
    ])

    assert answer == "review complete"
    system_messages = [m["content"] for m in captured["messages"] if m["role"] == "system"]
    assert any("Python desktop GUI engineering reference" in m for m in system_messages)
    assert any("Do not use Python `eval()`" in m for m in system_messages)


def test_unrelated_questions_do_not_load_python_gui_reference(monkeypatch):
    captured = {}

    def fake_request(messages, **kwargs):
        captured["messages"] = messages
        return "answer"

    monkeypatch.setattr(ai_engine, "_request", fake_request)
    ai_engine.ask_codeai([
        {"role": "user", "content": "Explain a SQL join"}
    ])

    system_messages = [m["content"] for m in captured["messages"] if m["role"] == "system"]
    assert not any("Python desktop GUI engineering reference" in m for m in system_messages)


def test_project_generation_receives_python_gui_reference(monkeypatch):
    captured = {}

    def fake_request(messages, **kwargs):
        captured["messages"] = messages
        return '{"project_name":"tk-calculator","description":"A calculator","files":[{"path":"app.py","content":"print(1)"}]}'

    monkeypatch.setattr(ai_engine, "_request", fake_request)
    project = ai_engine.generate_project("Create a Tkinter calculator")

    assert project["project_name"] == "tk-calculator"
    assert "Python desktop GUI engineering reference" in captured["messages"][0]["content"]
