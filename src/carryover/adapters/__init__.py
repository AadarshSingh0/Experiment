"""Platform adapters.

Each platform gets a *reader* (official export -> neutral bundle data) and/or a
*writer* (bundle -> whatever the target platform needs to rebuild the project).
v1 ships the claude.ai adapter; new platforms plug in here.
"""
