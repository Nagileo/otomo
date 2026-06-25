"""外部证据源工具的无网络测试。"""
from __future__ import annotations

from otomo.tools.erogamescape import build_erogamescape_tools
from otomo.tools.erogamescape.tool import _parse_results
from otomo.tools.yuc import build_yuc_tools
from otomo.tools.yuc.tool import _parse


def test_external_tool_builders():
    assert build_erogamescape_tools()[0].name == "search_erogamescape"
    assert build_yuc_tools()[0].name == "list_yuc_season"


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
