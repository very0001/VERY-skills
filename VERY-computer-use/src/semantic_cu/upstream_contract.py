"""Pinned input contracts for computer-use-linux 0.7.49.

Derived from the real packaged binary's tools/list response. Validation does not
consult backend.list_tools and never silently skips checks for a fake/backend
without discovery. These are strict advertised-field contracts, deliberately
stricter than JSON Schema's default allowance of unknown object properties.

The validator supports only the schema vocabulary used by this pinned catalog;
it does not fetch external references and is not a general JSON Schema engine.

Schema metadata attribution: agent-sh/computer-use-linux.

MIT License

Copyright (c) 2026 Avi Fenesh

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from __future__ import annotations

import copy
import json
import math
from typing import Any

UPSTREAM_VERSION = "0.7.49"
SOURCE_COMMIT = "6b288a604283a500a2254dcbe1433491746a76f5"
BINARY_SHA256 = "77202d572ffc6e1cc30a3d8df54264ba79b547fc9341a5743d293dccfe096eb0"

_INPUT_SCHEMAS = json.loads(r"""
{
  "activate_window": {
    "$defs": {
      "ScreenshotOutputFormat": {
        "enum": [
          "png",
          "jpeg"
        ],
        "type": "string"
      }
    },
    "properties": {
      "app_id": {
        "type": [
          "string",
          "null"
        ]
      },
      "format": {
        "anyOf": [
          {
            "$ref": "#/$defs/ScreenshotOutputFormat"
          },
          {
            "type": "null"
          }
        ]
      },
      "include_tree": {
        "type": [
          "boolean",
          "null"
        ]
      },
      "max_bytes": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_height": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_width": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "pid": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "quality": {
        "maximum": 95,
        "minimum": 1,
        "type": [
          "integer",
          "null"
        ]
      },
      "scale": {
        "format": "float",
        "type": [
          "number",
          "null"
        ]
      },
      "screenshot_after": {
        "anyOf": [
          {
            "type": "boolean"
          },
          {
            "enum": [
              "window",
              "screen"
            ],
            "type": "string"
          },
          {
            "type": "null"
          }
        ]
      },
      "terminal_command": {
        "type": [
          "string",
          "null"
        ]
      },
      "terminal_cwd": {
        "type": [
          "string",
          "null"
        ]
      },
      "terminal_pid": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "title": {
        "type": [
          "string",
          "null"
        ]
      },
      "tty": {
        "type": [
          "string",
          "null"
        ]
      },
      "window_id": {
        "maximum": 9007199254740991,
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "wm_class": {
        "type": [
          "string",
          "null"
        ]
      }
    },
    "type": "object"
  },
  "click": {
    "$defs": {
      "ClickCoordinateSpace": {
        "enum": [
          "capture",
          "atspi"
        ],
        "type": "string"
      },
      "ScreenshotOutputFormat": {
        "enum": [
          "png",
          "jpeg"
        ],
        "type": "string"
      }
    },
    "properties": {
      "app_id": {
        "type": [
          "string",
          "null"
        ]
      },
      "button": {
        "type": [
          "string",
          "null"
        ]
      },
      "click_count": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "coordinate_space": {
        "$ref": "#/$defs/ClickCoordinateSpace"
      },
      "element_index": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "format": {
        "anyOf": [
          {
            "$ref": "#/$defs/ScreenshotOutputFormat"
          },
          {
            "type": "null"
          }
        ]
      },
      "include_tree": {
        "type": [
          "boolean",
          "null"
        ]
      },
      "max_bytes": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_height": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_width": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "name": {
        "type": [
          "string",
          "null"
        ]
      },
      "pid": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "quality": {
        "maximum": 95,
        "minimum": 1,
        "type": [
          "integer",
          "null"
        ]
      },
      "relative": {
        "type": [
          "boolean",
          "null"
        ]
      },
      "role": {
        "type": [
          "string",
          "null"
        ]
      },
      "scale": {
        "format": "float",
        "type": [
          "number",
          "null"
        ]
      },
      "screenshot_after": {
        "anyOf": [
          {
            "type": "boolean"
          },
          {
            "enum": [
              "window",
              "screen"
            ],
            "type": "string"
          },
          {
            "type": "null"
          }
        ]
      },
      "snapshot_id": {
        "type": [
          "string",
          "null"
        ]
      },
      "states": {
        "items": {
          "type": "string"
        },
        "type": "array"
      },
      "text": {
        "type": [
          "string",
          "null"
        ]
      },
      "window_id": {
        "maximum": 9007199254740991,
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "window_title": {
        "type": [
          "string",
          "null"
        ]
      },
      "wm_class": {
        "type": [
          "string",
          "null"
        ]
      },
      "x": {
        "format": "int32",
        "type": [
          "integer",
          "null"
        ]
      },
      "y": {
        "format": "int32",
        "type": [
          "integer",
          "null"
        ]
      }
    },
    "type": "object"
  },
  "doctor": {
    "properties": {},
    "type": "object"
  },
  "drag": {
    "$defs": {
      "ScreenshotOutputFormat": {
        "enum": [
          "png",
          "jpeg"
        ],
        "type": "string"
      }
    },
    "properties": {
      "end_x": {
        "format": "int32",
        "type": "integer"
      },
      "end_y": {
        "format": "int32",
        "type": "integer"
      },
      "format": {
        "anyOf": [
          {
            "$ref": "#/$defs/ScreenshotOutputFormat"
          },
          {
            "type": "null"
          }
        ]
      },
      "include_tree": {
        "type": [
          "boolean",
          "null"
        ]
      },
      "max_bytes": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_height": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_width": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "quality": {
        "maximum": 95,
        "minimum": 1,
        "type": [
          "integer",
          "null"
        ]
      },
      "scale": {
        "format": "float",
        "type": [
          "number",
          "null"
        ]
      },
      "screenshot_after": {
        "anyOf": [
          {
            "type": "boolean"
          },
          {
            "enum": [
              "window",
              "screen"
            ],
            "type": "string"
          },
          {
            "type": "null"
          }
        ]
      },
      "start_x": {
        "format": "int32",
        "type": "integer"
      },
      "start_y": {
        "format": "int32",
        "type": "integer"
      }
    },
    "required": [
      "start_x",
      "start_y",
      "end_x",
      "end_y"
    ],
    "type": "object"
  },
  "focused_window": {
    "properties": {},
    "type": "object"
  },
  "get_app_state": {
    "$defs": {
      "ScreenshotOutputFormat": {
        "enum": [
          "png",
          "jpeg"
        ],
        "type": "string"
      }
    },
    "properties": {
      "app_id": {
        "type": [
          "string",
          "null"
        ]
      },
      "app_name_or_bundle_identifier": {
        "type": [
          "string",
          "null"
        ]
      },
      "format": {
        "anyOf": [
          {
            "$ref": "#/$defs/ScreenshotOutputFormat"
          },
          {
            "type": "null"
          }
        ]
      },
      "include_screenshot": {
        "type": [
          "boolean",
          "null"
        ]
      },
      "interactive_only": {
        "type": [
          "boolean",
          "null"
        ]
      },
      "max_bytes": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_depth": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_height": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_nodes": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_width": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "pid": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "quality": {
        "maximum": 95,
        "minimum": 1,
        "type": [
          "integer",
          "null"
        ]
      },
      "scale": {
        "format": "float",
        "type": [
          "number",
          "null"
        ]
      },
      "terminal_command": {
        "type": [
          "string",
          "null"
        ]
      },
      "terminal_cwd": {
        "type": [
          "string",
          "null"
        ]
      },
      "terminal_pid": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "title": {
        "type": [
          "string",
          "null"
        ]
      },
      "tty": {
        "type": [
          "string",
          "null"
        ]
      },
      "verbose": {
        "type": [
          "boolean",
          "null"
        ]
      },
      "window_id": {
        "maximum": 9007199254740991,
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "wm_class": {
        "type": [
          "string",
          "null"
        ]
      }
    },
    "type": "object"
  },
  "get_tree_page": {
    "properties": {
      "offset": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "snapshot_id": {
        "type": [
          "string",
          "null"
        ]
      }
    },
    "type": "object"
  },
  "list_apps": {
    "properties": {},
    "type": "object"
  },
  "list_windows": {
    "properties": {},
    "type": "object"
  },
  "move_window": {
    "$defs": {
      "ScreenshotOutputFormat": {
        "enum": [
          "png",
          "jpeg"
        ],
        "type": "string"
      }
    },
    "properties": {
      "app_id": {
        "type": [
          "string",
          "null"
        ]
      },
      "format": {
        "anyOf": [
          {
            "$ref": "#/$defs/ScreenshotOutputFormat"
          },
          {
            "type": "null"
          }
        ]
      },
      "include_tree": {
        "type": [
          "boolean",
          "null"
        ]
      },
      "max_bytes": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_height": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_width": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "pid": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "quality": {
        "maximum": 95,
        "minimum": 1,
        "type": [
          "integer",
          "null"
        ]
      },
      "scale": {
        "format": "float",
        "type": [
          "number",
          "null"
        ]
      },
      "screenshot_after": {
        "anyOf": [
          {
            "type": "boolean"
          },
          {
            "enum": [
              "window",
              "screen"
            ],
            "type": "string"
          },
          {
            "type": "null"
          }
        ]
      },
      "terminal_command": {
        "type": [
          "string",
          "null"
        ]
      },
      "terminal_cwd": {
        "type": [
          "string",
          "null"
        ]
      },
      "terminal_pid": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "title": {
        "type": [
          "string",
          "null"
        ]
      },
      "tty": {
        "type": [
          "string",
          "null"
        ]
      },
      "window_id": {
        "maximum": 9007199254740991,
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "wm_class": {
        "type": [
          "string",
          "null"
        ]
      },
      "x": {
        "format": "int32",
        "type": "integer"
      },
      "y": {
        "format": "int32",
        "type": "integer"
      }
    },
    "required": [
      "x",
      "y"
    ],
    "type": "object"
  },
  "perform_action": {
    "$defs": {
      "ScreenshotOutputFormat": {
        "enum": [
          "png",
          "jpeg"
        ],
        "type": "string"
      }
    },
    "properties": {
      "action": {
        "type": [
          "string",
          "null"
        ]
      },
      "app_id": {
        "type": [
          "string",
          "null"
        ]
      },
      "element_identifier": {
        "type": [
          "string",
          "null"
        ]
      },
      "element_index": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "format": {
        "anyOf": [
          {
            "$ref": "#/$defs/ScreenshotOutputFormat"
          },
          {
            "type": "null"
          }
        ]
      },
      "include_tree": {
        "type": [
          "boolean",
          "null"
        ]
      },
      "max_bytes": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_height": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_width": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "name": {
        "type": [
          "string",
          "null"
        ]
      },
      "pid": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "quality": {
        "maximum": 95,
        "minimum": 1,
        "type": [
          "integer",
          "null"
        ]
      },
      "role": {
        "type": [
          "string",
          "null"
        ]
      },
      "scale": {
        "format": "float",
        "type": [
          "number",
          "null"
        ]
      },
      "screenshot_after": {
        "anyOf": [
          {
            "type": "boolean"
          },
          {
            "enum": [
              "window",
              "screen"
            ],
            "type": "string"
          },
          {
            "type": "null"
          }
        ]
      },
      "snapshot_id": {
        "type": [
          "string",
          "null"
        ]
      },
      "states": {
        "items": {
          "type": "string"
        },
        "type": "array"
      },
      "text": {
        "type": [
          "string",
          "null"
        ]
      },
      "window_id": {
        "maximum": 9007199254740991,
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "window_title": {
        "type": [
          "string",
          "null"
        ]
      },
      "wm_class": {
        "type": [
          "string",
          "null"
        ]
      }
    },
    "type": "object"
  },
  "press_key": {
    "$defs": {
      "ScreenshotOutputFormat": {
        "enum": [
          "png",
          "jpeg"
        ],
        "type": "string"
      }
    },
    "properties": {
      "app_id": {
        "type": [
          "string",
          "null"
        ]
      },
      "format": {
        "anyOf": [
          {
            "$ref": "#/$defs/ScreenshotOutputFormat"
          },
          {
            "type": "null"
          }
        ]
      },
      "include_tree": {
        "type": [
          "boolean",
          "null"
        ]
      },
      "key": {
        "type": "string"
      },
      "max_bytes": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_height": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_width": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "pid": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "quality": {
        "maximum": 95,
        "minimum": 1,
        "type": [
          "integer",
          "null"
        ]
      },
      "scale": {
        "format": "float",
        "type": [
          "number",
          "null"
        ]
      },
      "screenshot_after": {
        "anyOf": [
          {
            "type": "boolean"
          },
          {
            "enum": [
              "window",
              "screen"
            ],
            "type": "string"
          },
          {
            "type": "null"
          }
        ]
      },
      "terminal_command": {
        "type": [
          "string",
          "null"
        ]
      },
      "terminal_cwd": {
        "type": [
          "string",
          "null"
        ]
      },
      "terminal_pid": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "title": {
        "type": [
          "string",
          "null"
        ]
      },
      "tty": {
        "type": [
          "string",
          "null"
        ]
      },
      "window_id": {
        "maximum": 9007199254740991,
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "wm_class": {
        "type": [
          "string",
          "null"
        ]
      }
    },
    "required": [
      "key"
    ],
    "type": "object"
  },
  "resize_window": {
    "$defs": {
      "ScreenshotOutputFormat": {
        "enum": [
          "png",
          "jpeg"
        ],
        "type": "string"
      }
    },
    "properties": {
      "app_id": {
        "type": [
          "string",
          "null"
        ]
      },
      "format": {
        "anyOf": [
          {
            "$ref": "#/$defs/ScreenshotOutputFormat"
          },
          {
            "type": "null"
          }
        ]
      },
      "height": {
        "format": "int32",
        "type": "integer"
      },
      "include_tree": {
        "type": [
          "boolean",
          "null"
        ]
      },
      "max_bytes": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_height": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_width": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "pid": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "quality": {
        "maximum": 95,
        "minimum": 1,
        "type": [
          "integer",
          "null"
        ]
      },
      "scale": {
        "format": "float",
        "type": [
          "number",
          "null"
        ]
      },
      "screenshot_after": {
        "anyOf": [
          {
            "type": "boolean"
          },
          {
            "enum": [
              "window",
              "screen"
            ],
            "type": "string"
          },
          {
            "type": "null"
          }
        ]
      },
      "terminal_command": {
        "type": [
          "string",
          "null"
        ]
      },
      "terminal_cwd": {
        "type": [
          "string",
          "null"
        ]
      },
      "terminal_pid": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "title": {
        "type": [
          "string",
          "null"
        ]
      },
      "tty": {
        "type": [
          "string",
          "null"
        ]
      },
      "width": {
        "format": "int32",
        "type": "integer"
      },
      "window_id": {
        "maximum": 9007199254740991,
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "wm_class": {
        "type": [
          "string",
          "null"
        ]
      }
    },
    "required": [
      "width",
      "height"
    ],
    "type": "object"
  },
  "screenshot": {
    "$defs": {
      "ScreenshotOutputFormat": {
        "enum": [
          "png",
          "jpeg"
        ],
        "type": "string"
      }
    },
    "properties": {
      "app_id": {
        "type": [
          "string",
          "null"
        ]
      },
      "format": {
        "anyOf": [
          {
            "$ref": "#/$defs/ScreenshotOutputFormat"
          },
          {
            "type": "null"
          }
        ]
      },
      "full_screen": {
        "type": [
          "boolean",
          "null"
        ]
      },
      "max_bytes": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_height": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_width": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "pid": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "quality": {
        "maximum": 95,
        "minimum": 1,
        "type": [
          "integer",
          "null"
        ]
      },
      "raise_window": {
        "type": [
          "boolean",
          "null"
        ]
      },
      "scale": {
        "format": "float",
        "type": [
          "number",
          "null"
        ]
      },
      "title": {
        "type": [
          "string",
          "null"
        ]
      },
      "window_id": {
        "maximum": 9007199254740991,
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "wm_class": {
        "type": [
          "string",
          "null"
        ]
      }
    },
    "type": "object"
  },
  "scroll": {
    "$defs": {
      "ScreenshotOutputFormat": {
        "enum": [
          "png",
          "jpeg"
        ],
        "type": "string"
      }
    },
    "properties": {
      "app_id": {
        "type": [
          "string",
          "null"
        ]
      },
      "direction": {
        "type": "string"
      },
      "element_index": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "format": {
        "anyOf": [
          {
            "$ref": "#/$defs/ScreenshotOutputFormat"
          },
          {
            "type": "null"
          }
        ]
      },
      "include_tree": {
        "type": [
          "boolean",
          "null"
        ]
      },
      "max_bytes": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_height": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_width": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "pages": {
        "format": "double",
        "maximum": 100,
        "minimum": -100,
        "type": [
          "number",
          "null"
        ]
      },
      "pid": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "quality": {
        "maximum": 95,
        "minimum": 1,
        "type": [
          "integer",
          "null"
        ]
      },
      "relative": {
        "type": [
          "boolean",
          "null"
        ]
      },
      "scale": {
        "format": "float",
        "type": [
          "number",
          "null"
        ]
      },
      "screenshot_after": {
        "anyOf": [
          {
            "type": "boolean"
          },
          {
            "enum": [
              "window",
              "screen"
            ],
            "type": "string"
          },
          {
            "type": "null"
          }
        ]
      },
      "snapshot_id": {
        "type": [
          "string",
          "null"
        ]
      },
      "window_id": {
        "maximum": 9007199254740991,
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "window_title": {
        "type": [
          "string",
          "null"
        ]
      },
      "wm_class": {
        "type": [
          "string",
          "null"
        ]
      },
      "x": {
        "format": "int32",
        "type": [
          "integer",
          "null"
        ]
      },
      "y": {
        "format": "int32",
        "type": [
          "integer",
          "null"
        ]
      }
    },
    "required": [
      "direction"
    ],
    "type": "object"
  },
  "set_value": {
    "$defs": {
      "ScreenshotOutputFormat": {
        "enum": [
          "png",
          "jpeg"
        ],
        "type": "string"
      }
    },
    "properties": {
      "app_id": {
        "type": [
          "string",
          "null"
        ]
      },
      "element_identifier": {
        "type": [
          "string",
          "null"
        ]
      },
      "element_index": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "format": {
        "anyOf": [
          {
            "$ref": "#/$defs/ScreenshotOutputFormat"
          },
          {
            "type": "null"
          }
        ]
      },
      "include_tree": {
        "type": [
          "boolean",
          "null"
        ]
      },
      "max_bytes": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_height": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_width": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "name": {
        "type": [
          "string",
          "null"
        ]
      },
      "pid": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "quality": {
        "maximum": 95,
        "minimum": 1,
        "type": [
          "integer",
          "null"
        ]
      },
      "role": {
        "type": [
          "string",
          "null"
        ]
      },
      "scale": {
        "format": "float",
        "type": [
          "number",
          "null"
        ]
      },
      "screenshot_after": {
        "anyOf": [
          {
            "type": "boolean"
          },
          {
            "enum": [
              "window",
              "screen"
            ],
            "type": "string"
          },
          {
            "type": "null"
          }
        ]
      },
      "snapshot_id": {
        "type": [
          "string",
          "null"
        ]
      },
      "states": {
        "items": {
          "type": "string"
        },
        "type": "array"
      },
      "text": {
        "type": [
          "string",
          "null"
        ]
      },
      "value": {
        "type": "string"
      },
      "window_id": {
        "maximum": 9007199254740991,
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "window_title": {
        "type": [
          "string",
          "null"
        ]
      },
      "wm_class": {
        "type": [
          "string",
          "null"
        ]
      }
    },
    "required": [
      "value"
    ],
    "type": "object"
  },
  "setup_accessibility": {
    "properties": {},
    "type": "object"
  },
  "setup_window_targeting": {
    "properties": {},
    "type": "object"
  },
  "type_text": {
    "$defs": {
      "ScreenshotOutputFormat": {
        "enum": [
          "png",
          "jpeg"
        ],
        "type": "string"
      }
    },
    "properties": {
      "app_id": {
        "type": [
          "string",
          "null"
        ]
      },
      "format": {
        "anyOf": [
          {
            "$ref": "#/$defs/ScreenshotOutputFormat"
          },
          {
            "type": "null"
          }
        ]
      },
      "include_tree": {
        "type": [
          "boolean",
          "null"
        ]
      },
      "max_bytes": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_height": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "max_width": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "pid": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "quality": {
        "maximum": 95,
        "minimum": 1,
        "type": [
          "integer",
          "null"
        ]
      },
      "scale": {
        "format": "float",
        "type": [
          "number",
          "null"
        ]
      },
      "screenshot_after": {
        "anyOf": [
          {
            "type": "boolean"
          },
          {
            "enum": [
              "window",
              "screen"
            ],
            "type": "string"
          },
          {
            "type": "null"
          }
        ]
      },
      "terminal_command": {
        "type": [
          "string",
          "null"
        ]
      },
      "terminal_cwd": {
        "type": [
          "string",
          "null"
        ]
      },
      "terminal_pid": {
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "text": {
        "type": "string"
      },
      "title": {
        "type": [
          "string",
          "null"
        ]
      },
      "tty": {
        "type": [
          "string",
          "null"
        ]
      },
      "window_id": {
        "maximum": 9007199254740991,
        "minimum": 0,
        "type": [
          "integer",
          "null"
        ]
      },
      "wm_class": {
        "type": [
          "string",
          "null"
        ]
      }
    },
    "required": [
      "text"
    ],
    "type": "object"
  }
}
""")


class ContractViolation(ValueError):
    """Arguments do not match the pinned upstream contract."""


def input_schema(tool: str) -> dict[str, Any]:
    """A private copy for diagnostics/tests, not a mutable live configuration."""
    if tool not in _INPUT_SCHEMAS:
        raise ContractViolation(f"No {UPSTREAM_VERSION} input contract for {tool}")
    return copy.deepcopy(_INPUT_SCHEMAS[tool])


def normalize_aliases(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Normalize only the documented title/window_title client aliases."""
    if not isinstance(arguments, dict):
        raise ContractViolation("arguments must be an object")
    schema = _INPUT_SCHEMAS.get(tool)
    if schema is None:
        raise ContractViolation(f"No {UPSTREAM_VERSION} input contract for {tool}")
    result = dict(arguments)
    fields = schema["properties"]
    canonical = (
        "window_title"
        if "window_title" in fields
        else "title"
        if "title" in fields
        else None
    )
    if canonical is not None:
        alias = "title" if canonical == "window_title" else "window_title"
        if alias in result:
            if canonical in result and result[canonical] != result[alias]:
                raise ContractViolation("conflicting title and window_title")
            result[canonical] = result.pop(alias)
    return result


def _same_json_value(left: Any, right: Any) -> bool:
    # bool is a subclass of int in Python but not a JSON integer.
    return type(left) is type(right) and left == right


def _is_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return type(value) is bool
    if expected == "integer":
        # Serde integer fields reject 1.0, despite JSON Schema's mathematical
        # integer interpretation. Match the actual wire parser here.
        return type(value) is int
    if expected == "number":
        return type(value) is int or (type(value) is float and math.isfinite(value))
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    raise ContractViolation(f"Unsupported pinned schema type: {expected}")


def _validate(
    value: Any, schema: dict[str, Any], root: dict[str, Any], path: str
) -> None:
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/$defs/"):
            raise ContractViolation("External schema references are not supported")
        name = ref.removeprefix("#/$defs/")
        definition = root.get("$defs", {}).get(name)
        if definition is None:
            raise ContractViolation("Pinned schema has an unresolved reference")
        _validate(value, definition, root, path)
    for union in ("anyOf", "oneOf"):
        if union in schema:
            matches = 0
            for candidate in schema[union]:
                try:
                    _validate(value, candidate, root, path)
                except ContractViolation:
                    continue
                matches += 1
            if not matches or (union == "oneOf" and matches != 1):
                raise ContractViolation(
                    f"{path} does not match the allowed types or options"
                )
    types = schema.get("type")
    if types is not None:
        choices = [types] if isinstance(types, str) else types
        if not any(_is_type(value, choice) for choice in choices):
            raise ContractViolation(f"{path} must have type {' or '.join(choices)}")
    if "enum" in schema and not any(
        _same_json_value(value, option) for option in schema["enum"]
    ):
        raise ContractViolation(f"{path} is not an allowed enum value")
    if "const" in schema and not _same_json_value(value, schema["const"]):
        raise ContractViolation(f"{path} does not match the allowed constant")
    if type(value) in (int, float):
        if type(value) is float and not math.isfinite(value):
            raise ContractViolation(f"{path} must be finite")
        if "minimum" in schema and value < schema["minimum"]:
            raise ContractViolation(f"{path} is below its minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ContractViolation(f"{path} exceeds its maximum")
        if schema.get("format") == "int32" and not -(2**31) <= value < 2**31:
            raise ContractViolation(f"{path} is outside the upstream int32 range")
    if isinstance(value, dict):
        properties = schema.get("properties")
        if properties is not None:
            unknown = set(value) - set(properties)
            if unknown:
                raise ContractViolation(
                    f"{path} contains unadvertised fields: {', '.join(sorted(map(str, unknown)))}"
                )
            missing = set(schema.get("required", [])) - set(value)
            if missing:
                raise ContractViolation(
                    f"{path} is missing required fields: {', '.join(sorted(missing))}"
                )
            for key, child in value.items():
                _validate(child, properties[key], root, f"{path}.{key}")
    if isinstance(value, list) and "items" in schema:
        for index, child in enumerate(value):
            _validate(child, schema["items"], root, f"{path}[{index}]")


def validate_arguments(tool: str, arguments: dict[str, Any]) -> None:
    """Reject unadvertised fields, missing required values and invalid shapes."""
    schema = _INPUT_SCHEMAS.get(tool)
    if schema is None:
        raise ContractViolation(f"No {UPSTREAM_VERSION} input contract for {tool}")
    _validate(arguments, schema, schema, f"{tool} arguments")
