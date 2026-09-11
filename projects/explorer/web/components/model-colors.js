export function modelFamilyColors(families) {
  return {type: "categorical", domain: [...new Set(families)].sort(),
    scheme: "tableau10", label: "Model family"};
}
