#!/bin/bash
# Unified test execution script
echo 'Running Unit Tests...'
python -m pytest unit_tests/
echo 'Running API Tests...'
python -m pytest API_tests/
