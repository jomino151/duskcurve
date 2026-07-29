"""flux-max GTK4/libadwaita GUI.

Importing this package MUST NOT import `gi`. Every module that touches
GTK/Gio imports it lazily, inside the method that needs it, exactly as
`fluxmax.daemon` does. Enforced by tests/gui/test_no_gi_import.py.
"""
