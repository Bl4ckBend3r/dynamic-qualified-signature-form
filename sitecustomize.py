"""Project-local sitecustomize intentionally left without monkey patches.

Older versions of the application modified Flask at import time from this
module. The refactored architecture registers behavior explicitly from the
application/services layer instead.
"""
