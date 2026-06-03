/* GOSE platform + input-mode model for the web UI.
   Mirrors scripts/gose_input.py. Detects PC vs device and the default/available
   navigation modes, and persists the user's choice. Load before other scripts:
     <script src="assets/platform.js"></script>
   Force a platform for testing with ?platform=device|pc  */
window.GOSE = (function () {
  const DEVICE = "device", PC = "pc";
  const NATIVE = "native", KEYBOARD = "keyboard", CONTROLLER = "controller";
  const DEFAULT = { device: NATIVE, pc: KEYBOARD };
  const AVAILABLE = { device: [NATIVE, CONTROLLER, KEYBOARD], pc: [KEYBOARD, CONTROLLER] };

  const qs = new URLSearchParams(location.search);
  // The PC app sets ?platform=pc (or localStorage). Default to PC — that's what
  // Zeke uses until the Odin 2 arrives.
  let platform = qs.get("platform") || localStorage.getItem("gose-platform") || PC;
  if (platform !== DEVICE && platform !== PC) platform = PC;
  localStorage.setItem("gose-platform", platform);

  const connectedControllers = () =>
    !!(navigator.getGamepads && [...navigator.getGamepads()].some(Boolean));

  function resolve(requested) {
    const remembered = localStorage.getItem("gose-input");
    let mode = [requested, remembered].find(m => m && AVAILABLE[platform].includes(m))
               || DEFAULT[platform];
    if (platform === PC && mode === CONTROLLER && !connectedControllers()) mode = KEYBOARD;
    return mode;
  }

  return {
    platform, DEVICE, PC, NATIVE, KEYBOARD, CONTROLLER,
    available: AVAILABLE[platform],
    default: DEFAULT[platform],
    autoAccepts: platform === DEVICE,
    connectedControllers,
    inputMode: () => resolve(null),
    setInput: (m) => localStorage.setItem("gose-input", m),
    resolve,
  };
})();
