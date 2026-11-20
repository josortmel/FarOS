# Lista blanca de vulture (#1361, 20-nov-2026): lo que vulture cree muerto y NO lo está.
# vulture solo LEE este fichero (no se ejecuta ni se importa); cada nombre que aparece aquí
# cuenta como «usado». Cada entrada lleva el motivo comprobado. Antes de añadir una, mira si
# es muerto de verdad: el 20-nov un aviso así escondía un fallo (cancelar un run → «error»).
# Se genera con: vulture faros tests tools --min-confidence 60 --ignore-decorators ... --make-whitelist

# sqlite3: el atributo lo lee la propia conexión al devolver filas, no nuestro código.
_.row_factory  # faros/db.py, faros/harness/opencode.py

# ctypes: campo de una estructura que se rellena para la API de Windows (JobObject).
_.LimitFlags  # faros/jobobj.py

# Campos de dataclasses que se leen por nombre, al convertirlas a diccionario (asdict) para la API.
json_schema  # harness/base.py Capabilities
effort  # harness/base.py Capabilities
system_prompt  # harness/base.py Capabilities
add_dir  # harness/base.py Capabilities
inherit_mcp  # harness/base.py Capabilities
accessible  # harness/model_discovery.py Model

# Parámetros de herramientas del MCP que la función recoge con locals() para mandarlos a la API
# (update_ticket, create_job, update_job en faros/mcp_server.py): vulture no ve locals().
next_due_override
verify_level
strict_mcp
