/*
 * Copyright 2026 Alexey Guseynov (kibergus). All Rights Reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 * ==============================================================================
 */

import globals from "globals";
import pluginJs from "@eslint/js";


/** @type {import('eslint').Linter.Config[]} */
export default [
  pluginJs.configs.recommended,
  {
    files: ["**/*.js"],
    languageOptions: {
      sourceType: "module",
      globals: {
        ...globals.browser,
        google: "readonly",
        Plotly: "readonly",
        Chart: "readonly",
        DRIVER_CONFIG: "readonly",
        KART_CONFIG: "readonly",
        SESSION_CONFIG: "readonly",
        L: "readonly", // Leaflet
        getMedian: "readonly",
        parseLapTime: "readonly",
      }
    },
    rules: {
      "no-unused-vars": ["warn", { "vars": "local", "args": "none" }],
      "no-undef": "error",
      "eqeqeq": "warn",
    }
  },
  {
    files: ["static/js/utils.js", "**/*.test.js"],
    languageOptions: {
      sourceType: "module",
      globals: {
        ...globals.browser,
        ...globals.node, // for tests
      }
    }
  }
];
