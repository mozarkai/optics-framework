const say = (text) => {
  const line = document.createElement("p");
  line.textContent = text;
  document.body.prepend(line);
};
say(matchMedia("(pointer: coarse)").matches ? "Phone layout" : "Desktop layout");
addEventListener("touchend", () => say("Touched"), { once: true });
addEventListener("scroll", () => say("Scrolled"), { once: true });
document.getElementById("buy").addEventListener("touchend", () => say("Bought"));
