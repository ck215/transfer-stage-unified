import re
with open('src/view.py', 'r') as f:
    content = f.read()

old_err = '''        if exception:
            full_message += f"\\n\\nDetails:\\n{type(exception).__name__}: {str(exception)}"'''
new_err = '''        if exception:
            if not full_message: full_message = ""
            try:
                full_message += f"\\n\\nDetails:\\n{type(exception).__name__}: {str(exception)}"
            except Exception:
                full_message += "\\n\\nDetails: <Unprintable Exception>"
        if full_message and len(full_message) > 5000:
            full_message = full_message[:5000] + "... [TRUNCATED]"'''
content = content.replace(old_err, new_err)

with open('src/view.py', 'w') as f:
    f.write(content)
