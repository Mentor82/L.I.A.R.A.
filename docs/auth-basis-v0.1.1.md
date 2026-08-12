Klar, Mirko — hier ist eine saubere **Copilot-ready Markdown-Spezifikation** für LIARA.

````markdown
# LIARA User/Auth-Architektur

## Ziel

Die Benutzerverwaltung von LIARA wird nicht im Frontend entschieden, sondern zentral im Backend durchgesetzt.

Grundsatz:

> Frontend zeigt. Backend entscheidet. Postgres speichert die Wahrheit.

---

## Rollen der Komponenten

| Komponente | Aufgabe |
|---|---|
| Frontend | Login-UI, Admin-Ansicht, Rollen anzeigen |
| API / Backend | Auth prüfen, Rechte erzwingen, Endpoints schützen |
| Auth-Service | Login, Token, Sessions, Rollenauflösung |
| Postgres | Persistente Wahrheit: User, Rollen, Rechte, Organisationen |
| Redis | Kurzlebige Sessions, Token-Blacklist, Rate-Limits |
| Audit-Log | Nachvollziehbarkeit sicherheitsrelevanter Aktionen |

---

## Grundprinzip

Alle sicherheitsrelevanten Entscheidungen passieren serverseitig.

Das Frontend darf niemals allein entscheiden:

- ob ein Nutzer Admin ist
- ob ein Button erlaubt ist
- ob ein API-Aufruf ausgeführt werden darf
- ob ein Tool verwendet werden darf
- ob ein Modell Zugriff auf Daten erhält

Frontend-Logik ist nur Komfort.  
Backend-Logik ist Sicherheit.

---

## Datenmodell in Postgres

### users

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
````

---

### organizations

```sql
CREATE TABLE organizations (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

### memberships

```sql
CREATE TABLE memberships (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(user_id, organization_id)
);
```

Beispiele für Rollen:

```text
owner
admin
developer
operator
viewer
guest
```

---

### permissions

```sql
CREATE TABLE permissions (
    id UUID PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,
    description TEXT
);
```

Beispiele:

```text
users.read
users.write
users.delete
models.use
tools.use
tools.admin
memory.read
memory.write
system.exec
audit.read
```

---

### role_permissions

```sql
CREATE TABLE role_permissions (
    role TEXT NOT NULL,
    permission_id UUID NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,

    PRIMARY KEY(role, permission_id)
);
```

---

### sessions

Optional in Postgres, wenn Sessions dauerhaft nachvollziehbar sein sollen.

```sql
CREATE TABLE sessions (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    refresh_token_hash TEXT NOT NULL,
    user_agent TEXT,
    ip_address TEXT,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

### audit_logs

```sql
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY,
    actor_user_id UUID REFERENCES users(id),
    organization_id UUID REFERENCES organizations(id),
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    ip_address TEXT,
    user_agent TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Beispiele:

```text
user.login
user.logout
user.created
user.role_changed
tool.executed
memory.accessed
system.command_denied
system.command_allowed
```

---

## Token-Konzept

### Access Token

Kurzlebig.

Empfohlen:

```text
5 bis 15 Minuten
```

Enthält nur notwendige Claims:

```json
{
  "sub": "user_id",
  "org": "organization_id",
  "role": "admin",
  "permissions": [
    "users.read",
    "tools.use"
  ],
  "exp": 1234567890
}
```

---

### Refresh Token

Langlebiger, aber serverseitig prüfbar.

Empfohlen:

```text
7 bis 30 Tage
```

Refresh Tokens werden nicht im Klartext gespeichert.

Nur Hash speichern:

```text
refresh_token_hash
```

---

## Redis-Nutzung

Redis ist nicht die Wahrheit.

Redis nutzt LIARA für:

```text
active_sessions
revoked_tokens
rate_limits
login_attempts
temporary_auth_state
```

Beispiel:

```text
auth:revoked:<jti> -> expires with token lifetime
rate:user:<user_id> -> counter
login:fail:<ip> -> counter
```

---

## Backend-Rechteprüfung

Jeder geschützte Endpoint braucht serverseitige Prüfung.

Beispiel:

```python
require_permission("users.write")
```

Oder für Tool-Aufrufe:

```python
require_permission("tools.use")
require_tool_policy(tool_name, user_context)
```

---

## Endpoint-Struktur

```text
POST   /auth/login
POST   /auth/logout
POST   /auth/refresh

GET    /me
GET    /me/permissions

GET    /admin/users
POST   /admin/users
PATCH  /admin/users/{id}
DELETE /admin/users/{id}

GET    /admin/roles
PATCH  /admin/users/{id}/role

GET    /admin/audit

POST   /chat
POST   /chat/stream
POST   /tools/run
```

---

## Sicherheitsregeln

### Regel 1

Kein Endpoint vertraut dem Frontend.

---

### Regel 2

Rollen aus dem Frontend werden ignoriert.

Das Backend liest Rollen immer aus:

```text
Token + Postgres + optional Redis Cache
```

---

### Regel 3

Tools brauchen eigene Rechteprüfung.

Ein normaler Chat-Zugriff bedeutet nicht automatisch Tool-Zugriff.

---

### Regel 4

Systemtools sind besonders geschützt.

Beispiel:

```text
system.exec
system.read_files
system.write_files
network.scan
```

Diese Rechte niemals pauschal an normale Nutzer geben.

---

### Regel 5

Audit bei allem Kritischen.

Auditpflichtig:

```text
Login
Logout
User-Änderung
Rollenänderung
Tool-Aufruf
Systemkommando
Memory-Zugriff
Fehlgeschlagene Rechteprüfung
```

---

## Rollenmodell Vorschlag

| Rolle     | Bedeutung                            |
| --------- | ------------------------------------ |
| owner     | Vollzugriff, Systembesitzer          |
| admin     | Benutzerverwaltung, Konfiguration    |
| developer | Modelle, Tools, Debug, Logs          |
| operator  | Nutzung, Monitoring, begrenzte Tools |
| viewer    | Lesen, keine Änderungen              |
| guest     | Stark eingeschränkter Zugriff        |

---

## Beispiel-Rechte

### owner

```text
*
```

### admin

```text
users.read
users.write
roles.read
audit.read
models.use
tools.use
memory.read
```

### developer

```text
models.use
tools.use
tools.debug
memory.read
memory.write
system.read_logs
```

### operator

```text
models.use
tools.use
monitoring.read
```

### viewer

```text
models.use
history.read
```

### guest

```text
chat.basic
```

---

## Wichtig für LIARA

Die Auth-Schicht darf nicht direkt mit LLM-Logik vermischt werden.

Saubere Trennung:

```text
Request
  -> API
  -> Auth prüfen
  -> UserContext bauen
  -> Orchestrator aufrufen
  -> Orchestrator entscheidet anhand UserContext
```

---

## UserContext

Der Orchestrator bekommt keinen rohen Token, sondern einen geprüften Kontext.

```json
{
  "user_id": "...",
  "organization_id": "...",
  "role": "developer",
  "permissions": [
    "models.use",
    "tools.use",
    "memory.read"
  ],
  "session_id": "...",
  "request_id": "..."
}
```

---

## Architekturfluss

```text
User
  ↓
Frontend
  ↓
API Gateway / Backend
  ↓
Auth Middleware
  ↓
UserContext
  ↓
Orchestrator
  ↓
Model Router / Tools / Memory
  ↓
Response
```

---

## Entscheidender Merksatz

> Postgres speichert, wer jemand ist.
> Redis merkt sich kurz, was gerade aktiv ist.
> Das Backend entscheidet, was erlaubt ist.
> Das Frontend zeigt nur an, was möglich ist.

```

Für LIARA würde ich das als **Version 0.1.1 Auth-Basis** einordnen.
```
