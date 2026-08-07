# Copyright 2026 Alexey Guseynov (kibergus). All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

import os
import shutil
from flask import Flask, render_template, send_from_directory, jsonify, abort, Response
from PIL import Image, ImageChops

from app import app as main_app
from tests.test_goldens import create_diff_image

# Create the isolated golden tests Flask application, fully nested inside goldens_diff
current_dir = os.path.dirname(os.path.abspath(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(current_dir, 'templates'),
    static_folder=os.path.join(current_dir, 'static')
)

GOLDEN_TESTS = [
    {
        'name': 'violin_default',
        'label': 'Violin Plot (Default Summary)',
        'url': '/violin_plot/fat_pro/cadet/2026-04-04/Shenington.png',
    },
    {
        'name': 'violin_session',
        'label': 'Violin Plot (Single Session)',
        'url': '/violin_plot/fat_pro/junior/2026-04-04/Shenington/15_34_pre_final.png',
    },
    {
        'name': 'violin_aspect',
        'label': 'Violin Plot (Custom Aspect 4:3)',
        'url': '/violin_plot/fat_pro/junior/2026-04-04/Shenington/15_34_pre_final.png?aspect=4:3',
    },
    {
        'name': 'violin_reverse',
        'label': 'Violin Plot (Reverse Track)',
        'url': '/violin_plot/fat_world_finals/cadet/2025-12-13/Willow Springs reverse/20_03_final_2.png',
    },
    {
        'name': 'gap_default',
        'label': 'Gap Plot (Single Session)',
        'url': '/gap_plot.png?session=fat_pro/junior/2026-04-04/Shenington/15_34_pre_final',
    },
    {
        'name': 'gap_aspect',
        'label': 'Gap Plot (Custom Aspect 4:2.5)',
        'url': '/gap_plot.png?session=fat_pro/junior/2026-04-04/Shenington/15_34_pre_final&aspect=4:2.5',
    },
    {
        'name': 'gap_multi',
        'label': 'Gap Plot (Multi-Session Combined)',
        'url': (
            '/gap_plot.png?session=fat_pro/junior/2026-04-04/Shenington/15_34_pre_final'
            '&session=fat_pro/junior/2026-04-04/Shenington/16_27_final'
        ),
    },
    {
        'name': 'gap_reverse',
        'label': 'Gap Plot (Reverse Track)',
        'url': '/gap_plot.png?session=fat_world_finals/cadet/2025-12-13/Willow Springs reverse/20_03_final_2',
    },
    {
        'name': 'combined_default',
        'label': 'Combined Plot (Violin + Gap, Aspect 4:5)',
        'url': '/combined_plot.png?session=fat_pro/junior/2026-04-04/Shenington/16_27_final&aspect=4:5',
    }
]


@app.route('/')
def golden_tests_view() -> str:
    golden_dir = os.path.join(current_dir, 'goldens')
    rendered_dir = os.path.join(golden_dir, 'rendered')

    os.makedirs(golden_dir, exist_ok=True)
    os.makedirs(rendered_dir, exist_ok=True)

    results = []
    for tc in GOLDEN_TESTS:
        name = tc['name']
        url = tc['url']

        golden_path = os.path.join(golden_dir, f'{name}.png')
        new_path = os.path.join(rendered_dir, f'{name}.png')
        diff_path = os.path.join(rendered_dir, f'{name}_diff.png')

        # Render dynamically if the rendered snapshot doesn't exist
        if not os.path.exists(new_path):
            with main_app.test_client() as client:
                res = client.get(url)
                if res.status_code == 200:
                    with open(new_path, 'wb') as f:
                        f.write(res.data)

        has_golden = os.path.exists(golden_path)
        has_new = os.path.exists(new_path)

        status = 'UNKNOWN'
        if not has_golden:
            status = 'MISSING'
        elif not has_new:
            status = 'ERROR'
        else:
            img_golden = Image.open(golden_path)
            img_new = Image.open(new_path)

            diff = ImageChops.difference(img_golden, img_new)
            if diff.getbbox() is None:
                status = 'PASSED'
            else:
                status = 'MISMATCH'
                diff_visual = create_diff_image(img_golden, img_new)
                diff_visual.save(diff_path)

        results.append({
            'name': name,
            'label': tc['label'],
            'url': url,
            'status': status,
            'has_golden': has_golden,
            'has_new': has_new,
            'has_diff': status == 'MISMATCH'
        })

    return render_template('golden_tests.html', results=results)


@app.route('/file/<type_>/<name>.png')
def serve_golden_test_file(type_: str, name: str) -> Response:
    golden_dir = os.path.join(current_dir, 'goldens')
    rendered_dir = os.path.join(golden_dir, 'rendered')

    name = os.path.basename(name)
    if not name.endswith('.png'):
        name = f'{name}.png'

    if type_ == 'golden':
        return send_from_directory(golden_dir, name)
    elif type_ == 'new':
        return send_from_directory(rendered_dir, name)
    elif type_ == 'diff':
        diff_name = name.replace('.png', '_diff.png')
        return send_from_directory(rendered_dir, diff_name)
    else:
        abort(404)


@app.route('/accept/<name>', methods=['POST'])
def accept_golden_test(name: str) -> Response | tuple[Response, int]:
    golden_dir = os.path.join(current_dir, 'goldens')
    rendered_dir = os.path.join(golden_dir, 'rendered')

    name = os.path.basename(name)

    golden_path = os.path.join(golden_dir, f'{name}.png')
    new_path = os.path.join(rendered_dir, f'{name}.png')
    diff_path = os.path.join(rendered_dir, f'{name}_diff.png')

    if not os.path.exists(new_path):
        return jsonify({'success': False, 'error': 'New rendered version not found.'}), 404

    try:
        shutil.copy2(new_path, golden_path)
        if os.path.exists(diff_path):
            os.remove(diff_path)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    app.run(port=5002, debug=False)
