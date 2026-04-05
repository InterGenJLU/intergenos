"""pkm verifier — Package integrity checking."""

from .database import PackageDB


class PackageVerifier:
    """Verify package integrity against the database."""

    def __init__(self, db: PackageDB):
        self.db = db

    def verify(self, name):
        """Verify a single package. Returns result dict or None if not found."""
        result = self.db.verify_package(name)
        if result is None:
            return None
        return result

    def verify_all(self):
        """Verify all installed packages.

        Returns: list of (name, version, result_dict)
        """
        results = []
        for pkg in self.db.list_installed():
            result = self.db.verify_package(pkg["name"])
            if result:
                results.append((pkg["name"], pkg["version"], result))
        return results
