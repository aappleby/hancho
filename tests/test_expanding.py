#!/usr/bin/python3
"""Tests for just the templating and text expansion part of Hancho"""

import sys
sys.path.append("..")
import hancho

print(hancho)


config = hancho.Config(
  foo = "1{bar}2",
  bar = "3{baz}4",
  baz = "5",
)

print(config)

hancho.expand_template(config, "{foo}")
