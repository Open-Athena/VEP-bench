export default {
  title: "VEPBench",
  root: "web",
  output: process.env.VEPBENCH_OBSERVABLE_OUTPUT ?? "dist",
  preserveExtension: true,
  home: "Leaderboard",
  theme: ["air", "near-midnight", "alt", "wide"],
  header: '<a href="./" style="font-weight: 700">VEPBench</a><a href="https://github.com/Open-Athena/VEPBench" target="_blank" rel="noreferrer" style="margin-left: auto">View source <span aria-hidden="true">↗</span></a>',
  pages: [
    {
      name: "Tasks",
      path: "/tasks",
      pages: [{
        name: "Consequence classification",
        path: "/tasks/consequence-classification"
      }, {
        name: "ClinVar",
        path: "/tasks/clinvar"
      }]
    },
    {name: "Questions", path: "/questions"}
  ],
  head: `
    <meta name="description" content="A public benchmark for native genetic variant effect prediction by language models.">
    <style>
      .vepbench-record-card {
        display: flex;
        flex-direction: column;
        height: min(70vh, 48rem);
        min-height: 0;
        overflow: hidden;
      }

      .vepbench-record-content {
        flex: 1 1 auto;
        min-height: 0;
        overflow-x: hidden;
        overflow-y: auto;
        overflow-wrap: anywhere;
        padding-right: 0.5rem;
      }

      .vepbench-record-content pre,
      .vepbench-record-content pre code {
        max-width: 100%;
        overflow-x: hidden;
        overflow-wrap: anywhere;
        white-space: pre-wrap;
        word-break: normal;
      }

      .vepbench-outcome-badge {
        display: inline-block;
        border-radius: 999px;
        font-weight: 600;
        line-height: 1.4;
        padding: 0.05rem 0.4rem;
      }

      .vepbench-outcome-correct {
        background: #e6f4ea;
        color: #0b5d1e;
      }

      .vepbench-outcome-incorrect {
        background: #fce8e6;
        color: #8a1c12;
      }

      .vepbench-outcome-format-failure {
        background: #fff3bf;
        color: #5c4600;
      }

      .vepbench-score-cell {
        background: color-mix(in srgb, currentColor 6%, transparent);
        border-radius: 0.2rem;
        display: block;
        min-width: 5rem;
        overflow: hidden;
        position: relative;
      }

      .vepbench-score-bar {
        background: #4267d2;
        bottom: 0;
        left: 0;
        opacity: 0.25;
        position: absolute;
        top: 0;
        width: var(--vepbench-score-width);
      }

      .vepbench-score-value {
        display: block;
        font-variant-numeric: tabular-nums;
        padding: 0 0.3rem;
        position: relative;
        text-align: center;
      }

      @media (prefers-color-scheme: dark) {
        .vepbench-score-bar {
          opacity: 0.4;
        }
      }

      @container (max-width: 639px) {
        .vepbench-record-card {
          height: auto;
          overflow: visible;
        }

        .vepbench-record-content {
          overflow-y: visible;
          padding-right: 0;
        }
      }
    </style>
  `
};
