# Databases on InterGenOS

Every major open-source database category is available to install with `pkm install`. Apart from SQLite — which is part of the base system, because a great deal of desktop software embeds it — no database server is pre-installed, so you choose exactly what your workload needs.

InterGenOS provides curated database packages across relational SQL, in-memory key-value cache, embedded key-value, time-series, and distributed key-value categories.

`pkm search` matches text in a package's name and description, so `pkm search
database` finds the packages that use that word and misses the ones that
describe themselves as a key-value store — Valkey, Memcached, LevelDB, RocksDB
and etcd among them. It is a text search, not a category listing. The tables in
this guide name every database package the release carries; search for one by
name (`pkm search valkey`) to see its version and tier. `pkm info valkey`
shows repository licence, size, and checksum while it is available but not
installed; after installation it reports installed metadata, dependencies, and
files instead.

## 1. Quick Chooser

Not sure which one to pick? Match your primary use case to the recommended package:

| Primary Need | Recommended Package | Alternate Options |
|---|---|---|
| **Persistent application data, SQL** | postgresql | mariadb (for MySQL compatibility) |
| **Embedded SQL in your app** | sqlite | *(Already included in the base system)* |
| **In-memory cache** | valkey | memcached |
| **Embedded KV store in your app** | leveldb | rocksdb (High-performance, complex) |
| **Time-series data (metrics, events)** | influxdb | - |
| **Distributed cluster coordination** | etcd | - |

## 2. Category Overview

### Relational SQL (postgresql, mariadb, sqlite)
If you're building a web app and want a traditional SQL database that handles concurrent connections and transactions, choose **PostgreSQL**. If your existing tooling strictly expects a MySQL-like backend, choose **MariaDB**. If you are building a standalone desktop application or a small script that needs a self-contained local database file, use **SQLite**, which is already included in the base system.

### In-Memory Cache (valkey, memcached)
If you need to temporarily cache session data or frequent query results to dramatically speed up your application, an in-memory Key-Value store is required. We recommend **Valkey**, a high-performance open-source fork of Redis. **Memcached** is a simpler, highly effective alternative for pure string caching.

### Embedded Key-Value (leveldb, rocksdb)
If you are developing software in C/C++ or Rust and need to embed a lightning-fast, persistent key-value store directly into your application binary without running a separate server process, choose **LevelDB** for stability and simplicity, or **RocksDB** for maximum performance and highly concurrent SSD workloads.

### Specialized Workloads (influxdb, etcd)
- **InfluxDB 3 Core**: Choose this if you are recording thousands of timestamped data points per second, such as server metrics or IoT sensor data.
- **etcd**: Choose this if you are building distributed systems (like Kubernetes clusters) and need a reliable, highly available key-value store to manage configuration and coordinate nodes.

## 3. Licensing Transparency

Every curated database package is open-source or public-domain software. Its exact licence or dedication is recorded in package metadata; SQLite, for example, uses `LicenseRef-Public-Domain` rather than an OSI licence identifier.

Where a popular database has moved to a restrictive non-OSI license, we ship an open-source, wire-compatible alternative instead. For example, **Valkey** is the recommended in-memory cache as a drop-in alternative to Redis.

Packages that require separate licence acceptance declare an explicit EULA or payload-licence gate. Ordinary `license` metadata is descriptive and does not by itself create an acceptance prompt.

## 4. Installation and Setup

Installing a database is as simple as running:
```bash
sudo pkm install postgresql
```

For an in-memory cache, install Valkey the same way:
```bash
sudo pkm install valkey
```

**Data Paths & Networking:**
- Database data files land in standard paths (typically `/var/lib/<database>`).
- By default, server databases bind only to loopback (`127.0.0.1` and, where configured, `::1`). They do not listen on non-loopback interfaces until you deliberately configure them to do so.

## 5. Security Defaults

Security is not first. It is only. Database packages ship conservative local
defaults, but authentication and TLS setup differ by server:

- **Loopback Only**: Server defaults bind to local loopback addresses to prevent accidental public exposure.
- **Authentication is explicit**: PostgreSQL's initialization helper prompts for the database-superuser password, and MariaDB generates an initial root password. Valkey deliberately ships with no shared archive-baked password and relies on loopback binding plus protected mode; Memcached builds SASL support but does not enable it in the shipped configuration. Configure authentication before exposing either service beyond loopback.
- **TLS support is package-specific**: The servers that support TLS are built with it, but no universal certificate directory or ready-to-enable certificate configuration is promised. Follow the server's own setup instructions and supply your certificates before opening a network listener.
- **AppArmor Confined**: Each database service runs under a strict AppArmor profile in enforce mode, limiting what files and capabilities the process can access if compromised.
- **Systemd Hardening**: Services use strict systemd isolation directives (e.g., ProtectSystem=strict, PrivateTmp=yes) to sandbox the execution environment.

## 6. Further Reading

- Need to understand how pkm verifies these packages before installing? See the [Repository Trust Model](../repository-trust.md).
- Brand new to InterGenOS? Check out the [Getting Started Guide](../getting-started.md).
