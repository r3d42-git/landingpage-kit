const copyButton = document.getElementById("copy");
const promptText = document.getElementById("auftrag");
const statusText = document.getElementById("copystatus");
copyButton.hidden = false;
copyButton.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(promptText.textContent.trim());
    statusText.textContent = "Installationsauftrag kopiert.";
  } catch {
    const range = document.createRange();
    range.selectNodeContents(promptText);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    statusText.textContent = "Der Auftrag ist markiert. Kopiere ihn über das Kontextmenü oder die Tastatur.";
  }
});
