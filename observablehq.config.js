export default {
  title: "VEPBench",
  root: "web",
  output: process.env.VEPBENCH_OBSERVABLE_OUTPUT ?? "dist",
  style: "styles.css",
  sidebar: false,
  pager: false,
  toc: false,
  preserveExtension: true,
  pages: [{name: "Questions", path: "/questions"}],
  head: `
    <meta name="description" content="A public benchmark for native genetic variant effect prediction by language models.">
    <meta name="color-scheme" content="light">
  `,
  header: ({path}) => `
    <div class="lab-header">
      <a class="lab-brand" href="./index.html" aria-label="VEPBench leaderboard">
        <span class="lab-mark">VEP</span>
        <span><strong>VEPBench</strong><small>CHR17 DEVELOPMENT ASSAY</small></span>
      </a>
      <nav aria-label="Primary">
        <a href="./index.html"${path === "/index" ? ' aria-current="page"' : ""}>Leaderboard</a>
        <a href="./questions.html"${path === "/questions" ? ' aria-current="page"' : ""}>Questions</a>
      </nav>
    </div>
  `,
  footer: `VEPBench · public development set · deterministic exact-match scoring`
};
