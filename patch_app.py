with open('src/app.py', 'r') as f:
    content = f.read()

old_parse = '''                if "Joy" in controllerID:
                    controllerID = int(controllerID.replace("Joy ", ""))'''
new_parse = '''                if isinstance(controllerID, str) and "Joy" in controllerID:
                    try:
                        prefix = controllerID.split(":")[0]
                        controllerID = int(prefix.replace("Joy ", "").strip())
                    except ValueError:
                        controllerID = None'''
content = content.replace(old_parse, new_parse)

with open('src/app.py', 'w') as f:
    f.write(content)
