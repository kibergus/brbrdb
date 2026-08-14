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
import pytest
from PIL import Image, ImageChops
import app

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'goldens_diff', 'goldens')
RENDERED_DIR = os.path.join(GOLDEN_DIR, 'rendered')

GOLDEN_TESTS = [
    {
        'name': 'violin_default',
        'url': '/violin_plot/fat_pro/cadet/2026-04-04/Shenington.png',
    },
    {
        'name': 'violin_session',
        'url': '/violin_plot/fat_pro/junior/2026-04-04/Shenington/15_34_pre_final.png',
    },
    {
        'name': 'violin_aspect',
        'url': '/violin_plot/fat_pro/junior/2026-04-04/Shenington/15_34_pre_final.png?aspect=4:3',
    },
    {
        'name': 'violin_reverse',
        'url': '/violin_plot/fat_world_finals/cadet/2025-12-13/Willow Springs reverse/20_03_final_2.png',
    },
    {
        'name': 'gap_default',
        'url': '/gap_plot.png?session=fat_pro/junior/2026-04-04/Shenington/15_34_pre_final',
    },
    {
        'name': 'gap_aspect',
        'url': '/gap_plot.png?session=fat_pro/junior/2026-04-04/Shenington/15_34_pre_final&aspect=4:2.5',
    },
    {
        'name': 'gap_multi',
        'url': (
            '/gap_plot.png?session=fat_pro/junior/2026-04-04/Shenington/15_34_pre_final'
            '&session=fat_pro/junior/2026-04-04/Shenington/16_27_final'
        ),
    },
    {
        'name': 'gap_reverse',
        'url': '/gap_plot.png?session=fat_world_finals/cadet/2025-12-13/Willow Springs reverse/20_03_final_2',
    },
    {
        'name': 'combined_default',
        'url': '/combined_plot.png?session=fat_pro/junior/2026-04-04/Shenington/16_27_final&aspect=4:5',
    },
    {
        'name': 'combined_maxy5',
        'url': (
            '/combined_plot.png?maxy=5'
            '&session=fat_pro/cadet/2026-08-08/Bayford Meadows/17_13_race_5_cadet_final'
            '&aspect=4:5'
        ),
    }
]


def create_diff_image(img_golden: Image.Image, img_new: Image.Image) -> Image.Image:
    if img_golden.size != img_new.size:
        img_new = img_new.resize(img_golden.size)

    # Convert to RGBA
    img_golden = img_golden.convert('RGBA')
    img_new = img_new.convert('RGBA')

    diff = ImageChops.difference(img_golden, img_new)

    # Convert golden to grayscale and dim it (luminance)
    background = img_golden.convert('L').convert('RGBA')
    background = Image.blend(background, Image.new('RGBA', background.size, (255, 255, 255, 255)), 0.6)

    # Get mask of different pixels
    diff_gray = diff.convert('L')
    mask = diff_gray.point(lambda x: 255 if x > 2 else 0)

    # Neon magenta overlay for difference
    highlight = Image.new('RGBA', background.size, (255, 0, 128, 255))

    # Composite
    diff_visual = Image.composite(highlight, background, mask)
    return diff_visual


@pytest.mark.parametrize('test_case', GOLDEN_TESTS, ids=lambda x: x['name'])
def test_golden_plot(test_case: dict[str, str]) -> None:
    name = test_case['name']
    url = test_case['url']

    os.makedirs(GOLDEN_DIR, exist_ok=True)
    os.makedirs(RENDERED_DIR, exist_ok=True)

    # Render new plot
    with app.app.test_client() as client:
        response = client.get(url)
        assert response.status_code == 200, f'Failed to render plot: {response.status_code}'
        new_bytes = response.data

    new_path = os.path.join(RENDERED_DIR, f'{name}.png')
    with open(new_path, 'wb') as f:
        f.write(new_bytes)

    golden_path = os.path.join(GOLDEN_DIR, f'{name}.png')
    if not os.path.exists(golden_path):
        pytest.fail(
            f"Golden version for '{name}' does not exist. "
            f'The new image must be reviewed by a human. '
            f'The verification dashboard will launch automatically to let you initialize it.'
        )

    img_golden = Image.open(golden_path)
    img_new = Image.open(new_path)

    diff = ImageChops.difference(img_golden, img_new)
    if diff.getbbox() is not None:
        diff_visual = create_diff_image(img_golden, img_new)
        diff_path = os.path.join(RENDERED_DIR, f'{name}_diff.png')
        diff_visual.save(diff_path)
        pytest.fail(
            f"Plot '{name}' does not match golden version! "
            f'Mismatching images must be reviewed by a human. '
            f'The verification dashboard will launch automatically to let you review the 3-panel diff and accept it.'
        )
