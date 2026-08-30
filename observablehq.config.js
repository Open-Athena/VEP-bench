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
