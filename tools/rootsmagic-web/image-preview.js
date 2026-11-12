(() => {
  const dialog = document.getElementById("image-preview");
  const title = document.getElementById("image-preview-title");
  const status = document.getElementById("image-preview-status");
  if (!(dialog instanceof HTMLDialogElement) || !title || !status) return;

  let opener = null;
  let currentImage = null;

  document.addEventListener("click", (event) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (!(event.target instanceof Element)) return;
    const link = event.target.closest("a[data-image-preview]");
    if (!(link instanceof HTMLAnchorElement) || link.hasAttribute("download")) return;
    const url = new URL(link.href);
    if (url.origin !== window.location.origin) return;

    event.preventDefault();
    opener = link;
    const caption = link.querySelector("img")?.alt || link.textContent.trim() || "Image preview";
    title.textContent = caption;
    status.textContent = "Loading image…";
    status.hidden = false;
    const image = new Image();
    image.id = "image-preview-image";
    image.alt = caption;
    image.hidden = true;
    currentImage?.remove();
    currentImage = image;
    status.after(image);
    image.addEventListener("load", () => {
      if (currentImage !== image) return;
      image.hidden = false;
      status.hidden = true;
    });
    image.addEventListener("error", () => {
      if (currentImage !== image) return;
      status.textContent = "This image could not be loaded. Close the preview and try again.";
    });
    dialog.showModal();
    image.src = url.href;
  });

  dialog.addEventListener("close", () => {
    const image = currentImage;
    currentImage = null;
    image?.removeAttribute("src");
    image?.remove();
    status.textContent = "";
    title.textContent = "Image preview";
    if (opener?.isConnected) opener.focus({ preventScroll: true });
    opener = null;
  });
})();
