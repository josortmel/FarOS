"""#922 (2-nov-2026): the run judge read output.md cut to EVIDENCE_MAX without knowing it. Run 67 (the magazine):
11,828 chars, «Fuentes consultadas» starting at 7,901 → the judge saw 99 chars of the section and failed it for
«missing sources» that were there. When the evidence is cut, the prompt must SAY so: how much it sees out of how
much, and where the whole file is (the judge can read that folder: _dirs_del_run)."""

from faros import verifier

JOB = {"id": 13, "name": "Revista", "prompt": "haz la revista", "verify_criteria": "todas las fuentes con ok"}


def _run(tmp_path, texto):
    out = tmp_path / "output.md"
    out.write_text(texto, encoding="utf-8")
    return {"id": 67, "output_path": str(out), "result_summary": texto[:500]}


def test_cut_evidence_says_it_is_cut_and_where_the_whole_file_is(tmp_path):
    texto = "x" * (verifier.EVIDENCE_MAX + 3828)
    run = _run(tmp_path, texto)
    prompt = verifier.build_run_prompt(None, run, JOB)
    assert "EVIDENCIA CORTADA" in prompt
    assert f"{verifier.EVIDENCE_MAX} de {len(texto)}" in prompt
    assert run["output_path"] in prompt


def test_whole_evidence_carries_no_cut_notice(tmp_path):
    run = _run(tmp_path, "corto y entero")
    prompt = verifier.build_run_prompt(None, run, JOB)
    assert "EVIDENCIA CORTADA" not in prompt
    assert "corto y entero" in prompt


def test_the_job_prompt_reaches_the_judge_whole(tmp_path):
    """#922, second half (3-nov): runs 64 and 65 failed for «the ticket's fixed source huggingface.co/zai-or».
    The job says zai-org: the judge got the prompt cut to 3,000 chars, which then fell inside that URL. Today
    it falls inside the list of fixed sources. A ticket's description goes whole; a job's prompt too."""
    fuentes = " , ".join(f"https://ejemplo{i}.org/fuente-fija" for i in range(150))
    job = {**JOB, "prompt": "Fuentes fijas: " + fuentes + " , https://huggingface.co/zai-org (fin)"}
    assert len(job["prompt"]) > 3000
    prompt = verifier.build_run_prompt(None, _run(tmp_path, "corto"), job)
    assert "https://huggingface.co/zai-org (fin)" in prompt
