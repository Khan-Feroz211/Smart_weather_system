#!/usr/bin/env python
"""Check app initialization and list routes."""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from app_clean import app, initialize_app
initialize_app()
print('App initialized successfully')
print(f'Routes: {len(list(app.url_map.iter_rules()))}')
for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
    methods = sorted(rule.methods - {'HEAD', 'OPTIONS'})
    print(f'  {",".join(methods)} {rule.rule}')
