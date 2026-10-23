"""Tests contra Postgres real.

Se activan solo con `TEST_DATABASE_URL` (ver conftest.py). Cada assertion que
falla acá es un bug de producción: no hay mocks, hay el motor.
"""
