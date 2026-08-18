-- FR/LG daycare egg status monitor.
--
-- This is meant for calibration work. It updates once per frame and shows:
-- - whether FR/LG currently has a pending daycare egg
-- - the stored lower 16-bit offspring personality value
-- - steps until the next daycare egg-generation check
-- - the daycare egg hatch-cycle counter byte
--
-- Important note:
-- mGBA's Lua API in this workspace exposes text buffers through the scripting
-- console, not a direct game-viewport text overlay API. So this renders live
-- in a script buffer that updates every frame.

local SAVEBLOCK1_PTR = 0x03005008
local DAYCARE_OFFSET = 0x2F80
local OFFSPRING_PERSONALITY_OFFSET = 0x118
local STEP_COUNTER_OFFSET = 0x11A
local MON2_STEPS_OFFSET = 0x114

local statusBuffer = nil
local active = false
local lastMessage = nil

local function writeTestMarker(lines)
	-- Keep deployment testing automatic without changing the visible behavior for
	-- normal use. When MGBA_LUA_STATUS_MARKER is set, mirror the current buffer
	-- text into that file so the Python test suite can verify the live Lua path.
	local ok, path = pcall(function()
		return os and os.getenv and os.getenv("MGBA_LUA_STATUS_MARKER")
	end)
	if not ok or not path or path == "" then
		return
	end

	pcall(function()
		local handle = io.open(path, "w")
		if not handle then
			return
		end
		for _, line in ipairs(lines) do
			handle:write(line)
			handle:write("\n")
		end
		handle:close()
	end)
end

local function detectFRLG()
	-- Identify FR/LG by the internal header fields mGBA exposes to Lua.
	local gameCode = emu:getGameCode()
	local gameTitle = emu:getGameTitle()

	-- mGBA's Lua API exposes the full internal code, e.g. "AGB-BPGE", not only
	-- the short "BPG*" suffix. Accept both forms so the script keeps working if
	-- another local build trims the Nintendo platform prefix differently.
	local frlgByCode = gameCode == "AGB-BPRE"
		or gameCode == "AGB-BPGE"
		or gameCode == "BPRE"
		or gameCode == "BPGE"
	local frlgByTitle = gameTitle == "POKEMON FIRE"
		or gameTitle == "POKEMON LEAF"

	return frlgByCode or frlgByTitle, gameCode, gameTitle
end

local function readDaycareStatus()
	-- Read the exact SaveBlock1 daycare fields the calibration workflow cares about.
	local saveBlock1 = emu:read32(SAVEBLOCK1_PTR)
	if saveBlock1 == 0 then
		return nil, "SaveBlock1 pointer is not ready yet"
	end

	local daycare = saveBlock1 + DAYCARE_OFFSET
	local offspringPersonality = emu:read16(daycare + OFFSPRING_PERSONALITY_OFFSET)
	local hatchStepCounter = emu:read8(daycare + STEP_COUNTER_OFFSET)
	local mon2Steps = emu:read32(daycare + MON2_STEPS_OFFSET)
	local lowByte = mon2Steps & 0xFF

	local stepsUntilNextEggCheck
	if offspringPersonality ~= 0 then
		stepsUntilNextEggCheck = 0
	else
		-- FR/LG increments the second daycare mon's step counter every player
		-- step and checks for egg generation when the low byte becomes 0xFF.
		if lowByte == 0xFF then
			stepsUntilNextEggCheck = 256
		else
			stepsUntilNextEggCheck = 0xFF - lowByte
		end
	end

	return {
		saveBlock1 = saveBlock1,
		offspringPersonality = offspringPersonality,
		eggWaiting = offspringPersonality ~= 0,
		hatchStepCounter = hatchStepCounter,
		stepsUntilNextEggCheck = stepsUntilNextEggCheck,
		mon2Steps = mon2Steps,
	}
end

local function ensureBuffer()
	-- Reuse one named scripting buffer so the monitor updates in place each frame.
	if statusBuffer then
		return statusBuffer
	end
	statusBuffer = console:createBuffer("FRLG Daycare Egg")
	statusBuffer:setSize(44, 8)
	return statusBuffer
end

local function renderMessage(lines)
	-- Mirror the latest status text into the scripting buffer and optional test hook.
	local buffer = ensureBuffer()
	buffer:clear()
	for _, line in ipairs(lines) do
		buffer:print(line .. "\n")
	end
	writeTestMarker(lines)
end

local function renderStatus()
	-- Refresh the live status panel from the current emulated frame.
	if not active then
		return
	end

	local status, err = readDaycareStatus()
	if not status then
		local message = "Waiting: " .. err
		if message ~= lastMessage then
			console:warn(message)
			lastMessage = message
		end
		renderMessage({
			"FR/LG Daycare Egg Status",
			"",
			message,
		})
		return
	end

	lastMessage = nil

	local eggText = status.eggWaiting and "YES" or "NO"
	local checkText
	if status.eggWaiting then
		checkText = "waiting for pickup"
	else
		checkText = tostring(status.stepsUntilNextEggCheck)
	end

	renderMessage({
		"FR/LG Daycare Egg Status",
		"",
		string.format("Egg waiting: %s", eggText),
		string.format("Lower half: 0x%04X", status.offspringPersonality),
		string.format("Steps to egg check: %s", checkText),
		string.format("Daycare hatch counter: %d", status.hatchStepCounter),
		string.format("Mon2 steps low byte: 0x%02X", status.mon2Steps & 0xFF),
		string.format("Frame: %d", emu:currentFrame()),
	})
end

local function startMonitor()
	-- Start monitoring only when a supported FR/LG ROM is loaded.
	local isFRLG, gameCode, gameTitle = detectFRLG()
	if emu:platform() ~= C.PLATFORM.GBA or not isFRLG then
		active = false
		renderMessage({
			"FR/LG Daycare Egg Status",
			"",
			"Unsupported game.",
			"Load FireRed or LeafGreen.",
			string.format("Code: %s", gameCode or "(none)"),
			string.format("Title: %s", gameTitle or "(none)"),
		})
		console:error(string.format(
			"FRLG Daycare Egg Status: unsupported game (code=%s title=%s)",
			gameCode or "(none)",
			gameTitle or "(none)"))
		return
	end

	active = true
	console:log(string.format(
		"FRLG Daycare Egg Status: monitoring active (code=%s title=%s)",
		gameCode or "(none)",
		gameTitle or "(none)"))
	renderStatus()
end

local function stopMonitor()
	-- Leave one short message behind so manual users can tell the monitor stopped.
	active = false
	if statusBuffer then
		statusBuffer:clear()
		statusBuffer:print("FRLG Daycare Egg Status\n\nStopped.\n")
	end
	console:log("FRLG Daycare Egg Status: monitoring stopped")
end

callbacks:add("start", startMonitor)
callbacks:add("reset", startMonitor)
callbacks:add("stop", stopMonitor)
callbacks:add("frame", renderStatus)

if emu then
	startMonitor()
end
