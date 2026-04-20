# BLFS Package Database — Design Plan

## Concept

Parse the BLFS 13.0 HTML book into a structured SQLite database. Every package query becomes a SQL statement instead of a marathon through 230K lines of HTML.

## Schema

```sql
-- Core package information
CREATE TABLE packages (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    version TEXT NOT NULL,
    section TEXT,                -- e.g., "Networking Libraries", "X Libraries"
    download_url TEXT,
    download_size TEXT,
    md5 TEXT,
    disk_space TEXT,
    build_time TEXT,             -- SBU estimate
    license TEXT,
    homepage TEXT,
    description TEXT
);

-- Dependencies with type classification
CREATE TABLE dependencies (
    id INTEGER PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES packages(id),
    depends_on TEXT NOT NULL,    -- package name (may not be in our tree)
    dep_type TEXT NOT NULL,      -- 'required', 'recommended', 'optional'
    note TEXT,                   -- e.g., "for X11 forwarding", "required for GNOME"
    UNIQUE(package_id, depends_on)
);

-- Patches listed in BLFS
CREATE TABLE patches (
    id INTEGER PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES packages(id),
    filename TEXT NOT NULL,
    url TEXT,
    required BOOLEAN DEFAULT TRUE
);

-- Build instructions (configure flags, special steps)
CREATE TABLE build_instructions (
    id INTEGER PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES packages(id),
    phase TEXT NOT NULL,         -- 'pre_configure', 'configure', 'build', 'install', 'post_install'
    commands TEXT NOT NULL       -- actual shell commands from BLFS
);

-- Test information
CREATE TABLE tests (
    id INTEGER PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES packages(id),
    command TEXT,                -- e.g., "make check", "ninja test"
    guidance TEXT,               -- BLFS text about the test
    known_failures TEXT,         -- known failure notes
    requirements TEXT,           -- e.g., "graphical session", "network access"
    critical BOOLEAN DEFAULT FALSE
);

-- InterGenOS tracking — maps BLFS packages to our tree
CREATE TABLE igos_packages (
    id INTEGER PRIMARY KEY,
    blfs_package_id INTEGER REFERENCES packages(id),
    tier TEXT NOT NULL,          -- 'core', 'base', 'desktop'
    our_version TEXT,
    status TEXT,                 -- 'built', 'planned', 'not_included'
    deviations TEXT              -- notes on where we differ from BLFS
);

-- Useful views
CREATE VIEW missing_deps AS
SELECT p.name as package, d.depends_on, d.dep_type
FROM dependencies d
JOIN packages p ON d.package_id = p.id
WHERE d.depends_on NOT IN (
    SELECT name FROM packages
    WHERE name IN (SELECT DISTINCT depends_on FROM dependencies)
);

CREATE VIEW dep_chain AS
-- Recursive CTE for full dependency chains
WITH RECURSIVE chain(name, dep, dep_type, depth) AS (
    SELECT p.name, d.depends_on, d.dep_type, 1
    FROM dependencies d JOIN packages p ON d.package_id = p.id
    WHERE p.name = :package_name
    UNION ALL
    SELECT c.name, d.depends_on, d.dep_type, c.depth + 1
    FROM chain c
    JOIN packages p2 ON p2.name = c.dep
    JOIN dependencies d ON d.package_id = p2.id
    WHERE c.depth < 20
)
SELECT * FROM chain;
```

## Parser Approach

The BLFS HTML has a consistent structure per package:
1. Package name + version in `<h2>` or `<h3>` with `<a id="pkgname">`
2. Download info in `<ul class="compact">`
3. Dependencies under `<h5>` headers: "Required", "Recommended", "Optional"
4. Build commands in `<pre class="userinput">` and `<pre class="root">`
5. Test info between build and install sections
6. Command explanations in `<div class="commands">`

**Implementation:** Python script using `html.parser` or `BeautifulSoup`:
1. Parse all `<a id="...">` anchors to find package sections
2. Extract structured data from each section
3. Insert into SQLite
4. Cross-reference against our package.yml files

**Estimated effort:** ~200-300 lines of Python, 2-3 hours to get right.

## Example Queries

```sql
-- "What does adding package X cost?"
WITH RECURSIVE chain AS (...)
SELECT DISTINCT dep FROM chain WHERE dep NOT IN
    (SELECT name FROM igos_packages WHERE status='built');

-- "Which of our packages are missing BLFS-recommended deps?"
SELECT ip.tier, p.name, d.depends_on
FROM igos_packages ip
JOIN packages p ON ip.blfs_package_id = p.id
JOIN dependencies d ON d.package_id = p.id
WHERE d.dep_type = 'recommended'
AND d.depends_on NOT IN (SELECT name FROM igos_packages WHERE status='built');

-- "Show all packages that need patches"
SELECT p.name, pt.filename, pt.url FROM patches pt
JOIN packages p ON pt.package_id = p.id;

-- "What tests should we be running?"
SELECT p.name, t.command, t.known_failures
FROM tests t JOIN packages p ON t.package_id = p.id
WHERE t.critical = TRUE;
```

## Integration Points

1. **Build system:** Before building a package, query its deps and warn about gaps
2. **Package manager:** Dependency resolution uses the DB as metadata
3. **Auditing:** Compare our package.yml deps against BLFS deps automatically
4. **Version tracking:** Flag packages where our version differs from BLFS
5. **CI/CD:** Automated validation of package configurations

## Maintenance

- Re-parse when BLFS updates (new release)
- Manual additions for InterGenOS-specific metadata
- `igos_packages` table updated by the build system as packages complete
