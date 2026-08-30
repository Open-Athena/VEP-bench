export default {
  title: "VEPBench",
  root: "web",
  output: "dist",
  style: "styles.css",
  sidebar: false,
  pager: false,
  toc: false,
  pages: [{name: "Questions", path: "/questions"}],
  head: `
    <meta name="description" content="A public benchmark for native genetic variant effect prediction by language models.">
    <meta name="color-scheme" content="light">
  `,
  header: ({path}) => `
    <div class="lab-header">
      <a class="lab-brand" href="./" aria-label="VEPBench leaderboard">
        <span class="lab-mark">VEP</span>
        <span><strong>VEPBench</strong><small>CHR17 DEVELOPMENT ASSAY</small></span>
      </a>
      <nav aria-label="Primary">
        <a href="./"${path === "/" ? ' aria-current="page"' : ""}>Leaderboard</a>
        <a href="./questions"${path === "/questions" ? ' aria-current="page"' : ""}>Questions</a>
      </nav>
    </div>
  `,
  footer: `VEPBench · public development set · deterministic exact-match scoring`
};
