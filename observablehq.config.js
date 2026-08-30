export default {
  title: "VEPBench",
  root: "web",
  output: process.env.VEPBENCH_OBSERVABLE_OUTPUT ?? "dist",
  preserveExtension: true,
  pages: [{name: "Questions", path: "/questions"}],
  head: `
    <meta name="description" content="A public benchmark for native genetic variant effect prediction by language models.">
  `
};
