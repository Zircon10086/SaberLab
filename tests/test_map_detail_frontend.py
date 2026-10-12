"""Map-detail JavaScript state regressions, exercised with Node and a tiny DOM stub.

The production functions are extracted from app.js without replacing their bodies.
Real rendering and animation acceptance stays in the standalone WebView2 harness.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "frontend" / "app.js"
NODE = shutil.which("node")


def function_source(name: str) -> str:
    source = JS_PATH.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith((f"function {name}(", f"async function {name}(")):
            end = index + 1
            while lines[end].rstrip("\r\n") != "}":
                end += 1
            return "".join(lines[index:end + 1])
    raise AssertionError(f"production function not found: {name}")


@unittest.skipUnless(NODE, "Node is required for frontend state regression tests")
class MapDetailStateTest(unittest.TestCase):
    def run_js(self, functions: tuple[str, ...], body: str) -> None:
        script = '"use strict"; const assert=require("node:assert/strict");\n'
        script += "\n(async()=>{\n"
        script += "\n".join(function_source(name) for name in functions)
        script += "\n" + body + "\n})().catch(e=>{console.error(e);process.exitCode=1;});"
        result = subprocess.run([NODE, "-"], input=script, text=True, encoding="utf-8",
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
                                check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cached_stats_do_not_write_current_page_state(self):
        self.run_js(("mapChartStats",), """
            const cached={series:{density_t:[1,2],density:[3,4]}};
            const old={series:{density_t:[9,10],density:[5,6]}};
            const _mapChartCache=new Map([['map',{Expert:cached}]]);
            const currentMapDetail={chartSeries:old.series};
            let calls=0;
            const api=async()=>{calls++;throw new Error('unexpected request');};
            const result=await mapChartStats('map','Expert',new AbortController().signal);
            assert.equal(result,cached);
            assert.equal(calls,0);
            assert.equal(currentMapDetail.chartSeries,old.series);
        """)

    def test_aborted_response_cannot_populate_chart_cache(self):
        self.run_js(("mapChartStats",), """
            const _mapChartCache=new Map();
            const abort=new AbortController();
            let complete;
            const response=new Promise(resolve=>{complete=resolve;});
            const api=()=>response;
            const pending=mapChartStats('map','Expert',abort.signal);
            abort.abort();
            const stats={series:{density_t:[1]}};
            complete(stats);
            assert.equal(await pending,stats);
            assert.equal(_mapChartCache.size,0);
        """)

    def test_cache_and_request_commit_through_current_difficulty(self):
        self.run_js(("renderMapDifficultyPage", "cancelMapChartRequest"), """
            let mapChartReqSeq=0,mapDetailReqSeq=1,mapChartAbort=null,currentMapDifficulty='Expert';
            const currentMapDetail={map_hash:'map',difficulties:[{key:'Expert'},{key:'Hard'}]};
            const page={innerHTML:'',childElementCount:0,querySelectorAll:()=>[]};
            const $=sel=>sel==='#md-page'?page:null;
            const resetDetailFade=()=>{};
            const renderDetailTransition=(el,render,slow,isCurrent=()=>true)=>{if(isCurrent())render();};
            // The commit path slides the page in (`slideDifficultyPage`); with no track in this
            // stub it renders straight through, which is the same thing the real one does when
            // there is nothing on screen yet.
            const slideDifficultyPage=(render)=>{render();};
            const mapDifficultyPageHtml=(row,diff)=>diff.key;
            const drawMapTimeline=()=>{};
            const fixMapDetailLayout=()=>{};
            const bindReplayItems=()=>{};
            const t=key=>key;
            const escHtml=value=>value;
            const MAP_DETAIL_SKELETON_DELAY=10;
            const pending=[];
            const mapChartStats=(hash,key,signal)=>new Promise((resolve,reject)=>pending.push({key,signal,resolve,reject}));
            const first=renderMapDifficultyPage();
            currentMapDifficulty='Hard';
            const second=renderMapDifficultyPage();
            assert.equal(pending[0].signal.aborted,true);
            const hard={key:'Hard',series:{density:[8]},history:[]};
            pending[1].resolve(hard);
            await second;
            assert.equal(currentMapDetail.chartSeries,hard.series);
            pending[0].resolve({key:'Expert',series:{density:[99]}});
            await first;
            assert.equal(page.innerHTML,'Hard');
            assert.equal(currentMapDetail.chartSeries,hard.series);
            currentMapDifficulty='Expert';
            const third=renderMapDifficultyPage();
            currentMapDifficulty='Hard';
            const fourth=renderMapDifficultyPage();
            pending[3].resolve(hard);
            pending[2].reject(new Error('old error'));
            await Promise.all([third,fourth]);
            assert.equal(page.innerHTML,'Hard');
            assert.equal(currentMapDetail.chartSeries,hard.series);
        """)

    def test_replay_tab_changes_are_scoped_to_the_replay_container(self):
        self.run_js(("switchDetailPane",), """
            const selected=[];
            const mapTab={dataset:{diff:'Expert'},classList:{toggle:()=>{throw new Error('map tab mutated');}}};
            const replayTabs=['data','ai','replay'].map(pane=>({dataset:{pane},classList:{toggle:(name,on)=>{if(on)selected.push(pane);}}}));
            const $$=sel=>sel==='#detail-tabs .dt-tab'?replayTabs:[...replayTabs,mapTab];
            const track={classList:{add:()=>{},remove:()=>{}},style:{}};
            const $=sel=>sel==='#panes-track'?track:null;
            const syncChroPlayback=()=>{};
            switchDetailPane('ai');
            assert.deepEqual(selected,['ai']);
            assert.equal(track.style.transform,'translateX(-33.3333%)');
        """)

    def test_freeze_layout_measures_each_chart_only_once(self):
        self.run_js(("freezeDetailCharts",), """
            const chart={style:{},offsetHeight:275};
            const root={querySelectorAll:()=>[chart]};
            assert.equal(freezeDetailCharts(root),true);
            assert.equal(chart.style.height,'275px');
            chart.offsetHeight=600;
            assert.equal(freezeDetailCharts(root),false);
            assert.equal(chart.style.height,'275px');
        """)

    def test_empty_selection_can_be_enabled_again(self):
        self.run_js(("drawMapTimeline",), """
            const box={innerHTML:''};
            const controls=[{checked:false,value:'density'}];
            const $=()=>box;
            const $$=()=>controls;
            const currentMapDetail={chartSeries:{density_t:[0,1],density:[2,3]}};
            const MDCHART_LABELS={density:()=> 'density'};
            const MDCHART_COLORS={density:'#888'};
            const t=key=>key;
            const fmt={dur2:v=>String(v)};
            let calls=0;
            const lineChart=()=>{calls++;};
            drawMapTimeline(false);
            assert.equal(box.innerHTML.includes('mdchart.none_selected'),true);
            assert.equal(typeof controls[0].onchange,'function');
            controls[0].checked=true;
            controls[0].onchange();
            assert.equal(calls,1);
        """)

    def test_fade_restart_cancels_old_cleanup(self):
        self.run_js(("resetDetailFade", "renderDetailTransition"), """
            const detailFadeTimers=new WeakMap();
            const timers=new Map();let next=0;
            const setTimeout=(fn,ms)=>{timers.set(++next,fn);return next;};
            const clearTimeout=id=>timers.delete(id);
            const classes=new Set();
            const content={classList:{add:(...xs)=>xs.forEach(x=>classes.add(x)),remove:(...xs)=>xs.forEach(x=>classes.delete(x))},offsetWidth:20};
            let renders=0;
            renderDetailTransition(content,()=>{renders++;});
            const first=detailFadeTimers.get(content);
            renderDetailTransition(content,()=>{renders++;});
            assert.equal(timers.has(first),false);
            assert.equal(classes.has('detail-fade-in'),true);
            assert.equal(renders,2);
            resetDetailFade(content);
            assert.equal(timers.size,0);
            assert.equal(classes.size,0);
        """)

    def test_shared_styles_cover_both_details_and_chart_sizes(self):
        css = (ROOT / "frontend" / "style.css").read_text(encoding="utf-8")
        source = JS_PATH.read_text(encoding="utf-8")
        self.assertIn(".dpane .surface, #md-page .surface", css)
        self.assertIn(":is(#detail-content, #mapdetail-content, #md-page)", css)
        self.assertIn('<div id="md-chart" class="chart"></div>', source)
        self.assertNotIn('<div id="md-chart" class="chart small"></div>', source)
        self.assertIn('"#detail-tabs .dt-tab"', source)
        self.assertNotIn('$$(".dt-tab")', source)
        self.assertIn('fixMapDetailLayout();\n        drawMapTimeline(false);', source)


    def test_map_cards_are_not_bound_as_replays(self):
        """A map card reuses `.replay-item` for its layout, so the replay bindings must exclude
        it: otherwise a click on a map opens a REPLAY detail (regression, measured 2026-10)."""
        source = JS_PATH.read_text(encoding="utf-8")
        self.assertIn('$$(".replay-item:not(.map-item)")', source)
        self.assertNotIn('$$(".replay-item").forEach', source)


if __name__ == "__main__":
    unittest.main()
