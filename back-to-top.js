const backTop = document.querySelector('.back-top');

if (backTop) {
  const toggleBackTop = () => backTop.classList.toggle('is-visible', window.scrollY > window.innerHeight * .7);
  window.addEventListener('scroll', toggleBackTop, { passive: true });
  toggleBackTop();
}
