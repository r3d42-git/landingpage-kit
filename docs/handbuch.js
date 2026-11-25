const copyButton = document.getElementById("copy");
const promptText = document.getElementById("auftrag");
const statusText = document.getElementById("copystatus");
const languageLink = document.querySelector("[data-language-link]");
const alternatePath = languageLink.getAttribute("href");

function updateLanguageLink() {
  const hash = window.location.hash;
  languageLink.setAttribute("href", document.getElementById(hash.slice(1)) ? alternatePath + hash : alternatePath);
}

updateLanguageLink();
window.addEventListener("hashchange", updateLanguageLink);

copyButton.hidden = false;
copyButton.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(promptText.textContent.trim());
    statusText.textContent = statusText.dataset.copied;
  } catch {
    const range = document.createRange();
    range.selectNodeContents(promptText);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    statusText.textContent = statusText.dataset.selected;
  }
});
