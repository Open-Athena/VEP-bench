export default {
  title: "VEPBench",
  root: "web",
  output: process.env.VEPBENCH_OBSERVABLE_OUTPUT ?? "dist",
  preserveExtension: true,
  pages: [{
    name: "Tasks",
    path: "/tasks",
    pages: [{
      name: "Consequence classification",
      path: "/tasks/consequence-classification"
    }]
  }],
  head: `
    <meta name="description" content="A public benchmark for native genetic variant effect prediction by language models.">
  `
};
