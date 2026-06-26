"""外部证据源工具的无网络测试。"""
from __future__ import annotations

from otomo.tools.erogamescape import build_erogamescape_tools
from otomo.tools.erogamescape.tool import _parse_rank_results, _parse_results
from otomo.tools.musicbrainz import build_musicbrainz_tools
from otomo.tools.musicbrainz.tool import _parse_musicbrainz_items
from otomo.tools.yuc import build_yuc_tools
from otomo.tools.yuc.tool import _parse


def test_external_tool_builders():
    assert build_erogamescape_tools()[0].name == "search_erogamescape"
    assert build_erogamescape_tools()[1].name == "rank_erogamescape"
    assert build_musicbrainz_tools()[0].name == "search_musicbrainz"
    assert build_yuc_tools()[0].name == "list_yuc_season"


def test_parse_musicbrainz_release_group():
    data = {
        "release-groups": [
            {
                "id": "abc",
                "title": "K-ON! MUSIC HISTORY'S BOX",
                "artist-credit": [{"artist": {"name": "Various Artists"}}],
                "first-release-date": "2013-03-20",
                "primary-type": "Album",
                "score": 92,
            }
        ]
    }
    items = _parse_musicbrainz_items(data, "release-group", 5)
    assert len(items) == 1
    assert items[0].artist == "Various Artists"
    assert items[0].url == "https://musicbrainz.org/release-group/abc"


def test_parse_erogamescape_search_row():
    html = """
    <table><tr><th>ゲーム名</th></tr>
    <tr>
      <td><a href="game.php?game=29089#ad">ATRI -My Dear Moments-</a><span>(非18禁)</span></td>
      <td><a href="brand.php?brand=6525">ANIPLEX.EXE</a></td>
      <td>2020-06-19</td><td>82</td><td>9</td><td>1235</td>
    </tr></table>
    """
    items = _parse_results(html, 5)
    assert len(items) == 1
    assert items[0].title == "ATRI -My Dear Moments-"
    assert items[0].median == 82
    assert items[0].vote_count == 1235


def test_parse_erogamescape_rank_row():
    html = """
    <table><tr><th>ゲーム名</th><th>ブランド名</th><th>中央値</th><th>平均値</th><th>標準偏差</th><th>データ数</th></tr>
    <tr>
      <td><a href="game.php?game=20764#ad">ランス10</a> OHP</td>
      <td>ALICESOFT</td><td>99</td><td>93.47</td><td>13</td><td>1138</td>
    </tr></table>
    """
    items = _parse_rank_results(html, 5, min_votes=30)
    assert len(items) == 1
    assert items[0].rank_position == 1
    assert items[0].average == 93.47
    assert items[0].brand == "ALICESOFT"


def test_parse_yuc_block():
    html = """
    <!--#A01-->
    <div style="float:left"><img width="180px" data-src="https://example.com/a.jpg"></div>
    <div><table width="500px"><tr><td class="title_main_r" colspan="2" rowspan="2">
    <p class="title_cn_r">再见 拉拉</p>
    <p class="title_jp_r">さよならララ</p></td>
    <td class="type_a_r">原创动画</td></tr>
    <tr><td class="type_tag_r">人鱼/恋爱/复古</td></tr><tr>
    <td rowspan="2" class="staff_r">动画制作：Kinema Citrus</td>
    <td rowspan="2" class="cast_r">菱川花菜　川石奈奈</td>
    <td class="link_a_r">
    <a href="https://goodbyelara.com/" target="_blank">动画官网</a><br>
    <a href="https://www.bilibili.com/video/BV1CfdBBQEr4/" target="_blank">PV</a>
    <p class="broadcast_r">7/5周日深夜</p></td></tr>
    </table></div>
    """
    items = _parse(html, 5)
    assert len(items) == 1
    assert items[0].title_cn == "再见 拉拉"
    assert items[0].studio == "Kinema Citrus"
    assert items[0].tags == ["人鱼", "恋爱", "复古"]
