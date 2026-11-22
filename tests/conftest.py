import os
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def chinese_font():
    for name in [os.environ.get("POSTER_TEST_FONT", ""), "C:/Windows/Fonts/msyh.ttc",
                 "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]:
        if name and Path(name).is_file():
            return Path(name)
    pytest.fail("Chinese test font missing; install fonts-noto-cjk or set POSTER_TEST_FONT")
