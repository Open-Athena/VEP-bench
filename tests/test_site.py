import json
from pathlib import Path

import pytest
from vepbench_explorer.config import load_site_config
from vepbench_explorer.site import (
    build_question_metadata,
    build_site,
    load_assay_publications,
)

from vepbench.errors import BuildError

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "projects/explorer/web"
SATMUT_SOURCE = ROOT / "data/sources/satmut-mpra-cadd-v1.7.jsonl"
SGE_SOURCE = ROOT / "data/sources/sge-mavedb-2026-09-03.jsonl"
OPENSPLICE_SOURCE = ROOT / "data/sources/opensplice-snv-figshare-v5.jsonl"
SITE_CONFIG = ROOT / "projects/explorer/config/site.yaml"
ASSAY_PUBLICATIONS = ROOT / "projects/explorer/config/assay-publications.yaml"
OFFICIAL_DATA_BASE_URL = (
    "https://huggingface.co/buckets/open-athena/VEP-bench/resolve/versions/main"
)


def production_question_metadata() -> dict:
    return build_question_metadata(
        source_paths=[OPENSPLICE_SOURCE, SATMUT_SOURCE, SGE_SOURCE],
        assay_publications=load_assay_publications(ASSAY_PUBLICATIONS),
    )


def test_site_stages_only_source_assets_and_official_main_config(tmp_path: Path) -> None:
    output = tmp_path / "site"
    metadata = production_question_metadata()
    config = build_site(
        assets_dir=ASSETS,
        output=output,
        data_base_url=OFFICIAL_DATA_BASE_URL,
        question_metadata=metadata,
    )

    assert config["data_base_url"] == OFFICIAL_DATA_BASE_URL
    assert json.loads((output / "data/config.json").read_text()) == config
    assert json.loads((output / "data/question-metadata.json").read_text()) == metadata
    assert set(metadata["by_task_family"]) == {"opensplice_snv", "satmut_mpra", "sge"}
    assert len(metadata["by_task_family"]["opensplice_snv"]) == 20
    assert len(metadata["by_task_family"]["satmut_mpra"]) == 16
    assert len(metadata["by_task_family"]["sge"]) == 15
    assert metadata["by_task_family"]["opensplice_snv"]["E01"]["assay_first_indexed"] == {
        "date": "2026-05-24",
        "kind": "dataset",
        "registry": "Figshare",
        "url": "https://doi.org/10.6084/m9.figshare.32337414.v5",
    }
    assert metadata["by_task_family"]["satmut_mpra"]["GP1BA"] == {
        "element": "GP1BB promoter",
        "assay_first_indexed": {
            "date": "2019-08-08",
            "kind": "paper",
            "registry": "PubMed",
            "url": "https://pubmed.ncbi.nlm.nih.gov/31395865/",
        },
    }
    assert metadata["by_task_family"]["sge"]["BAP1"]["element"] == "BAP1"
    assert metadata["by_task_family"]["sge"]["BAP1"]["assay_first_indexed"]["date"] == (
        "2024-07-05"
    )
    assert metadata["by_task_family"]["sge"]["VHL"]["assay_first_indexed"] == {
        "date": "2024-04-25",
        "kind": "assay_repository",
        "registry": "MaveDB",
        "url": "https://api.mavedb.org/api/v1/score-sets/urn%3Amavedb%3A00000675-a-1",
    }
    assert metadata["by_task_family"]["sge"]["BARD1"]["assay_first_indexed"] == {
        "date": "2026-06-08",
        "kind": "assay_repository",
        "registry": "MaveDB",
        "url": "https://api.mavedb.org/api/v1/score-sets/urn%3Amavedb%3A00001250-a-2",
    }
    assert (output / "index.md").is_file()
    assert not (output / "questions.md").exists()
    assert (output / "tasks/satmut-mpra.md").is_file()
    assert (output / "tasks/sge.md").is_file()
    assert (output / "tasks/opensplice-snv.md").is_file()
    assert not (output / "tasks/consequence-classification.md").exists()
    assert not (output / "tasks/clinvar.md").exists()
    assert not (output / "data/explorer.json").exists()
    assert not (output / "data/questions.jsonl").exists()
    assert not (output / "data/results").exists()
    leaderboard_source = (output / "index.md").read_text(encoding="utf-8")
    helper_source = (output / "components/vepbench.js").read_text(encoding="utf-8")
    assert 'label: "Task"' in leaderboard_source
    assert 'label: "Metric"' in leaderboard_source
    assert '{key: "spearman", label: "Spearman"}' in leaderboard_source
    assert '{key: "pearson", label: "Pearson"}' in leaderboard_source
    assert "supportsOverallLeaderboard(aggregation)" in leaderboard_source
    assert "? [null]" in leaderboard_source
    assert "value: allTasksAvailable ? null : taskOptions[0]" in leaderboard_source
    assert "macro-average ${scoreMetricLabel} correlation across tasks" in leaderboard_source
    assert 'columns: ["model", "score", "knowledge_cutoff", "tokens", "cost"]' in (
        leaderboard_source
    )
    assert 'knowledge_cutoff: "Knowledge cutoff"' in leaderboard_source
    assert "knowledge_cutoff: formatKnowledgeCutoff" in leaderboard_source
    assert 'return "Not disclosed"' in helper_source
    assert 'release_date: "Release date"' not in leaderboard_source
    assert "Unscored model attempts" in leaderboard_source
    assert 'status: "Content filtered"' in leaderboard_source
    assert "Claude Fable 5.1 (medium)" in leaderboard_source
    assert "Claude Opus 5 (medium)" in leaderboard_source
    assert "8/8 panels; zero output tokens; not ranked" in leaderboard_source
    assert "5/8 panels; run stopped and not ranked" in leaderboard_source
    assert "visibleUnscoredAttempts" not in leaderboard_source
    assert "visible regardless of the task selected" in leaderboard_source
    assert leaderboard_source.index("## Unscored model attempts") > leaderboard_source.index(
        "## Score by cost and token usage"
    )
    assert 'y: {\n      label: "Score"' in leaderboard_source
    assert "displayScore," in leaderboard_source
    assert "score: displayScore(row.score)" in leaderboard_source
    assert "const width = Math.max(0, Math.min(1, value)) * 100;" in leaderboard_source
    assert "domain: [0, 1]" not in leaderboard_source
    assert "(value + 1) / 2" not in leaderboard_source
    assert 'score: "Score"' in leaderboard_source
    assert "Valid outputs" not in leaderboard_source
    task_source = (output / "tasks/satmut-mpra.md").read_text(encoding="utf-8")
    assert "# Expression (satMutMPRA)" in task_source
    assert "https://doi.org/10.1038/s41467-019-11526-w" in task_source
    assert "const controls = view(controlsInput);" in task_source
    assert "const selected = view(questionTable);" in task_source
    assert 'assay_first_indexed: "Assay first indexed"' in task_source
    assert "assayFirstIndexedLink" in task_source
    assert 'spearman_rho: "Spearman \u03c1"' in task_source
    assert 'pearson_r: "Pearson r"' in task_source
    assert "rankingOutcomeMetrics(outcome)" in task_source
    assert 'cutoff_relation: "Cutoff relation"' in task_source
    assert '"All cutoff relations"' in task_source
    assert "assayCutoffRelation(entry.assay_first_indexed, knowledgeCutoff)" in task_source
    assert "knowledgeCutoffNote(selectedTaskRun)" in task_source
    assert "Generators.input" not in task_source
    opensplice_source = (output / "tasks/opensplice-snv.md").read_text(encoding="utf-8")
    assert "# Splicing (OpenSplice)" in opensplice_source
    assert "https://doi.org/10.6084/m9.figshare.32337414.v5" in opensplice_source
    assert "large measured 5th-to-95th-percentile" in opensplice_source
    assert "not direct estimates of native-tissue splicing" in opensplice_source
    component_source = (output / "components/vepbench.js").read_text(encoding="utf-8")
    assert 'element("h2", null, "Prompt given to model")' in component_source
    assert "markdownNode(question.prompt)" in component_source
    assert "Reference effects" not in component_source
    assert "npm:@observablehq/plot@0.6.17" in component_source
    assert 'label: "Measured effect"' in component_source
    assert "ariaLabel: (row)" in component_source
    assert 'element("h3", null, "Predictions vs. measurements")' in component_source
    assert "predictionComparisonPlot(comparisonRows)" in component_source
    assert "export function formatCorrelation(value)" in component_source
    assert "export function cutoffRelationBadge(value)" in component_source
    assert "export function knowledgeCutoffNote(run)" in component_source
    assert "dates in the cutoff month are gray and Unknown" in component_source
    sge_source = (output / "tasks/sge.md").read_text(encoding="utf-8")
    assert "# Fitness (SGE)" in sge_source
    assert "https://www.mavedb.org/" in sge_source
    assert 'const taskFamily = "sge";' in sge_source
    assert "gene: Inputs.select([" in sge_source
    assert "Reference effects" not in sge_source
    for source in (task_source, opensplice_source, sge_source):
        description = source.split("## Questions", maxsplit=1)[0]
        assert "## Task description" not in description
        assert "## Task design" not in description
        assert "## Interpretation" not in description
        assert '<div class="card">' not in description
        assert "<dl>" not in description
        assert "Task version" not in description
        assert "question schema" not in description
        assert "Public development set" not in description

    tasks_source = (output / "tasks.md").read_text(encoding="utf-8")
    assert (
        tasks_source.index("## Fitness (SGE)")
        < tasks_source.index("## Expression (satMutMPRA)")
        < tasks_source.index("## Splicing (OpenSplice)")
    )


def test_question_metadata_requires_display_metadata_for_every_task_record(tmp_path: Path) -> None:
    source = tmp_path / "future-task.jsonl"
    source.write_text(
        '{"source_record_id":"future-1","task_family":"future_task"}\n',
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(BuildError, match="missing display metadata"):
        build_question_metadata(
            source_paths=[source],
            assay_publications={
                "schema_version": "1.0",
                "by_task_family": {
                    "future_task": {
                        "default": {
                            "date": "2026-01-01",
                            "kind": "dataset",
                            "registry": "Example registry",
                            "url": "https://example.test/assay",
                        }
                    }
                },
            },
        )


def test_question_metadata_requires_assay_publication_for_every_task_record(
    tmp_path: Path,
) -> None:
    source = tmp_path / "future-task.jsonl"
    source.write_text(
        '{"source_record_id":"future-1","task_family":"future_task",'
        '"source_metadata":{"display_name":"Future assay"}}\n',
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(BuildError, match="missing assay publication metadata"):
        build_question_metadata(
            source_paths=[source],
            assay_publications={"schema_version": "1.0", "by_task_family": {}},
        )


def test_assay_publication_metadata_rejects_noncanonical_dates(tmp_path: Path) -> None:
    metadata = tmp_path / "assay-publications.yaml"
    metadata.write_text(
        """schema_version: \"1.0\"
by_task_family:
  future_task:
    default:
      date: \"2026-1-1\"
      kind: dataset
      registry: Example registry
      url: https://example.test/assay
""",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(BuildError, match="ISO 8601 calendar date"):
        load_assay_publications(metadata)


def test_site_config_resolves_assay_publication_metadata() -> None:
    settings = load_site_config(SITE_CONFIG)

    assert settings.assay_publications == ASSAY_PUBLICATIONS.resolve()


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/versions/main",
        "file:///tmp/versions/main",
    ],
)
def test_site_rejects_insecure_data_source(tmp_path: Path, url: str) -> None:
    with pytest.raises(BuildError, match="must use HTTPS"):
        build_site(assets_dir=ASSETS, output=tmp_path / "site", data_base_url=url)


def test_site_accepts_configured_https_data_source(tmp_path: Path) -> None:
    url = "https://example.test/versions/experiment"
    config = build_site(assets_dir=ASSETS, output=tmp_path / "site", data_base_url=url)

    assert config["data_base_url"] == url


def test_site_build_refuses_nonempty_output(tmp_path: Path) -> None:
    output = tmp_path / "site"
    output.mkdir()
    (output / "keep.txt").write_text("user data", encoding="utf-8")

    with pytest.raises(BuildError, match="refusing to overwrite"):
        build_site(assets_dir=ASSETS, output=output, data_base_url=OFFICIAL_DATA_BASE_URL)
