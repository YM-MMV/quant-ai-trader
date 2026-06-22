"""External resource management (M2).

Safely clone and inspect third-party repositories listed in
``config/external_repos.yaml``. This package only *manages* repos (manifest +
git command construction); it never imports third-party code, and nothing here
contacts the network at import time.
"""
