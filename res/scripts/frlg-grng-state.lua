-- FR/LG gRngValue console monitor.
--
-- Reads the live 32-bit `gRngValue` at 0x03005000 and prints it through the
-- mGBA scripting console. It also reverse-searches the nearby GBA LCRNG orbit
-- for the 16-bit seed state that produced the current value.

local GRNG_VALUE_ADDR = 0x03005000
local GBA_LCRNG_MULTIPLIER = 0x41C64E6D
local GBA_LCRNG_INCREMENT = 0x6073
local GBA_LCRNG_MULTIPLIER_INVERSE = 0xEEB9EB65
local DEFAULT_LOG_EVERY_FRAMES = 60
local DEFAULT_DISCERN_WINDOW = 131072
local DEFAULT_REVERSE_STEPS_PER_FRAME = 4096

local statusBuffer = nil
local active = false
local lastLoggedFrame = nil
local reverseSearch = nil

local function readEnvInteger(name, fallback, minimum)
	local ok, value = pcall(function()
		return os and os.getenv and os.getenv(name)
	end)
	if not ok or not value or value == "" then
		return fallback
	end
	local parsed = tonumber(value)
	if not parsed or parsed < minimum then
		return fallback
	end
	return math.floor(parsed)
end

local function writeTestMarker(lines)
	-- Optional deployment-test hook. Normal use only writes to the mGBA console.
	local ok, path = pcall(function()
		return os and os.getenv and os.getenv("MGBA_GRNG_STATUS_MARKER")
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

local function logEveryFrames()
	return readEnvInteger("MGBA_GRNG_LOG_EVERY", DEFAULT_LOG_EVERY_FRAMES, 1)
end

local function discernWindow()
	return readEnvInteger("MGBA_GRNG_DISCERN_WINDOW", DEFAULT_DISCERN_WINDOW, 1)
end

local function reverseStepsPerFrame()
	return readEnvInteger(
		"MGBA_GRNG_REVERSE_STEPS_PER_FRAME",
		DEFAULT_REVERSE_STEPS_PER_FRAME,
		1)
end

local function detectFRLG()
	local gameCode = emu:getGameCode()
	local gameTitle = emu:getGameTitle()
	local frlgByCode = gameCode == "AGB-BPRE"
		or gameCode == "AGB-BPGE"
		or gameCode == "BPRE"
		or gameCode == "BPGE"
	local frlgByTitle = gameTitle == "POKEMON FIRE"
		or gameTitle == "POKEMON LEAF"

	return frlgByCode or frlgByTitle, gameCode, gameTitle
end

local function readGrngValue()
	return emu:read32(GRNG_VALUE_ADDR) & 0xFFFFFFFF
end

local function lcrngStep(state, multiplier, increment)
	-- Split multiply into 16-bit chunks. This matches Real96's Lua approach and
	-- avoids relying on wide integer multiplication behavior for 32-bit modulo.
	state = state & 0xFFFFFFFF
	multiplier = multiplier & 0xFFFFFFFF
	local stateLow = state & 0xFFFF
	local stateHigh = state >> 16
	local multiplierLow = multiplier & 0xFFFF
	local multiplierHigh = multiplier >> 16
	local cross = multiplierHigh * stateLow + stateHigh * multiplierLow
	local value = multiplierLow * stateLow + (cross & 0xFFFF) * 0x10000 + increment
	return value & 0xFFFFFFFF
end

local function lcrngNextState(state)
	return lcrngStep(state, GBA_LCRNG_MULTIPLIER, GBA_LCRNG_INCREMENT)
end

local function lcrngPreviousState(state)
	local adjusted = ((state & 0xFFFFFFFF) - GBA_LCRNG_INCREMENT) & 0xFFFFFFFF
	return lcrngStep(adjusted, GBA_LCRNG_MULTIPLIER_INVERSE, 0)
end

local function resetReverseSearch(observed)
	reverseSearch = {
		observed = observed & 0xFFFFFFFF,
		backward = observed & 0xFFFFFFFF,
		forward = observed & 0xFFFFFFFF,
		searched = 0,
		maxSteps = discernWindow(),
		hitSeed = nil,
		signedSteps = nil,
		done = false,
	}

	if reverseSearch.observed <= 0xFFFF then
		reverseSearch.hitSeed = reverseSearch.observed & 0xFFFF
		reverseSearch.signedSteps = 0
		reverseSearch.done = true
	end
end

local function updateReverseSearch(observed)
	observed = observed & 0xFFFFFFFF
	if not reverseSearch or reverseSearch.observed ~= observed then
		resetReverseSearch(observed)
	end
	if reverseSearch.done then
		return reverseSearch
	end

	local target = math.min(reverseSearch.maxSteps, reverseSearch.searched + reverseStepsPerFrame())
	for steps = reverseSearch.searched + 1, target do
		reverseSearch.backward = lcrngPreviousState(reverseSearch.backward)
		if reverseSearch.backward <= 0xFFFF then
			reverseSearch.hitSeed = reverseSearch.backward & 0xFFFF
			reverseSearch.signedSteps = -steps
			reverseSearch.searched = steps
			reverseSearch.done = true
			return reverseSearch
		end

		reverseSearch.forward = lcrngNextState(reverseSearch.forward)
		if reverseSearch.forward <= 0xFFFF then
			reverseSearch.hitSeed = reverseSearch.forward & 0xFFFF
			reverseSearch.signedSteps = steps
			reverseSearch.searched = steps
			reverseSearch.done = true
			return reverseSearch
		end
	end

	reverseSearch.searched = target
	if reverseSearch.searched >= reverseSearch.maxSteps then
		reverseSearch.done = true
	end
	return reverseSearch
end

local function formatSigned(value)
	if value >= 0 then
		return string.format("+%d", value)
	end
	return tostring(value)
end

local function reverseLines(search)
	if search.hitSeed then
		-- "Frame distance" here is the signed LCRNG advance distance from the
		-- 16-bit seed state to live gRngValue. Noisy gameplay can spend more or
		-- fewer emulator frames than RNG calls, so this is still PRNG distance.
		return string.format("Seed16:  0x%04X", search.hitSeed),
			string.format("Frame distance: %s", formatSigned(-search.signedSteps))
	end
	if search.done then
		return "Seed16:  no hit",
			string.format("Scanned: +/- %d LCRNG", search.maxSteps)
	end
	return "Seed16:  scanning",
		string.format("Scanned: %d/%d", search.searched, search.maxSteps)
end

local function reverseLogText(search)
	if search.hitSeed then
		return string.format(
			" seed16=0x%04X frame_distance=%s",
			search.hitSeed,
			formatSigned(-search.signedSteps))
	end
	if search.done then
		return string.format(" seed16=no_hit scanned=+/- %d", search.maxSteps)
	end
	return string.format(" seed16=scanning scanned=%d/%d", search.searched, search.maxSteps)
end

local function ensureBuffer()
	if statusBuffer then
		return statusBuffer
	end
	statusBuffer = console:createBuffer("FRLG gRngValue")
	statusBuffer:setSize(48, 7)
	return statusBuffer
end

local function renderStatus(value, frame)
	local buffer = ensureBuffer()
	local search = updateReverseSearch(value)
	local seedLine, stepsLine = reverseLines(search)
	local lines = {
		"FR/LG gRngValue",
		"",
		string.format("Address: 0x%08X", GRNG_VALUE_ADDR),
		string.format("Value:   0x%08X", value),
		seedLine,
		stepsLine,
		string.format("Frame:   %d", frame),
	}

	buffer:clear()
	buffer:print(table.concat(lines, "\n"))
	buffer:print("\n")
	writeTestMarker(lines)
	return search
end

local function logStatus(value, frame, reason, search)
	console:log(string.format(
		"FRLG gRngValue %s: frame=%d addr=0x%08X value=0x%08X%s",
		reason,
		frame,
		GRNG_VALUE_ADDR,
		value,
		search and reverseLogText(search) or ""))
end

local function updateStatus()
	if not active then
		return
	end

	local frame = emu:currentFrame()
	local value = readGrngValue()
	local search = renderStatus(value, frame)

	local interval = logEveryFrames()
	if lastLoggedFrame == nil or frame - lastLoggedFrame >= interval then
		lastLoggedFrame = frame
		logStatus(value, frame, "sample", search)
	end
end

local function startMonitor()
	if emu:platform() ~= C.PLATFORM.GBA then
		active = false
		console:error("FRLG gRngValue: load a GBA ROM first")
		return
	end

	local isFRLG, gameCode, gameTitle = detectFRLG()
	if not isFRLG then
		active = false
		console:error(string.format(
			"FRLG gRngValue: unsupported game (code=%s title=%s)",
			gameCode or "(none)",
			gameTitle or "(none)"))
		return
	end

	active = true
	lastLoggedFrame = nil
	reverseSearch = nil
	local frame = emu:currentFrame()
	local value = readGrngValue()
	local search = renderStatus(value, frame)
	logStatus(value, frame, "start", search)
end

local function stopMonitor()
	active = false
	reverseSearch = nil
	if statusBuffer then
		statusBuffer:clear()
		statusBuffer:print("FR/LG gRngValue\n\nStopped.\n")
	end
	console:log("FRLG gRngValue: monitor stopped")
end

callbacks:add("start", startMonitor)
callbacks:add("reset", startMonitor)
callbacks:add("stop", stopMonitor)
callbacks:add("frame", updateStatus)

if emu then
	startMonitor()
end
