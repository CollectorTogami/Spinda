-- Built-in Lua input-tape helpers for the custom mGBA workspace.
--
-- This mirrors the local Python/Qt tape format closely:
-- - format marker: mgba-input-tape-v1
-- - anchor-agnostic: no ROM/save/savestate stored in the tape
-- - runs are RLE-compressed button masks
-- - replay clears stale keys at the boundaries by default

inputTape = (function()
	local M = {}

	M.FORMAT = "mgba-input-tape-v1"
	M.BUTTON_BITS = {
		A = 0,
		B = 1,
		SELECT = 2,
		START = 3,
		RIGHT = 4,
		LEFT = 5,
		UP = 6,
		DOWN = 7,
		R = 8,
		L = 9,
	}

	local BUTTON_ORDER = {
		"A",
		"B",
		"SELECT",
		"START",
		"RIGHT",
		"LEFT",
		"UP",
		"DOWN",
		"R",
		"L",
	}

	local BUTTON_ALIASES = {
		SEL = "SELECT",
		RETURN = "START",
		ENTER = "START",
		NONE = "NONE",
		NEUTRAL = "NONE",
		NOINPUT = "NONE",
		NO_INPUT = "NONE",
		["."] = "NONE",
		["-"] = "NONE",
		["0"] = "NONE",
	}

	local GBA_BUTTON_MASK = 0x03FF
	local KEYINPUT_ADDR = 0x04000130
	local DEFAULT_FORMAT_NOTE = "Anchor-agnostic tape: no ROM, save, or savestate path is stored."

	local function uint32(value)
		return (math.tointeger(tonumber(value) or 0) or 0) & 0xFFFFFFFF
	end

	local function bit_band(a, b)
		return uint32(a) & uint32(b)
	end

	local function bit_bor(a, b)
		return uint32(a) | uint32(b)
	end

	local function bit_lshift(value, shift)
		return uint32(value) << shift
	end

	local function trim(text)
		return tostring(text):match("^%s*(.-)%s*$")
	end

	local function option_value(opts, key, default)
		if type(opts) ~= "table" then
			return default
		end
		local value = opts[key]
		if value == nil then
			return default
		end
		return value
	end

	local function is_array(value)
		if type(value) ~= "table" then
			return false
		end
		local count = 0
		local maxIndex = 0
		for key, _ in pairs(value) do
			if type(key) ~= "number" then
				return false
			end
			if key < 1 or key % 1 ~= 0 then
				return false
			end
			if key > maxIndex then
				maxIndex = key
			end
			count = count + 1
		end
		return count == maxIndex
	end

	local function copy_json_value(value)
		local valueType = type(value)
		if valueType == "nil" or valueType == "boolean" or valueType == "number" or valueType == "string" then
			return value
		end
		if valueType ~= "table" then
			return tostring(value)
		end

		local copied = {}
		if is_array(value) then
			for i = 1, #value do
				copied[i] = copy_json_value(value[i])
			end
			return copied
		end

		for key, item in pairs(value) do
			copied[tostring(key)] = copy_json_value(item)
		end
		return copied
	end

	local function json_escape(text)
		return tostring(text)
			:gsub("\\", "\\\\")
			:gsub("\"", "\\\"")
			:gsub("\b", "\\b")
			:gsub("\f", "\\f")
			:gsub("\n", "\\n")
			:gsub("\r", "\\r")
			:gsub("\t", "\\t")
	end

	local function json_encode(value, depth)
		local valueType = type(value)
		depth = depth or 0
		if valueType == "nil" then
			return "null"
		end
		if valueType == "boolean" then
			return value and "true" or "false"
		end
		if valueType == "number" then
			if value ~= value or value == math.huge or value == -math.huge then
				error("cannot encode non-finite number in inputTape JSON")
			end
			if value % 1 == 0 then
				return string.format("%.0f", value)
			end
			return tostring(value)
		end
		if valueType == "string" then
			return "\"" .. json_escape(value) .. "\""
		end
		if valueType ~= "table" then
			error("cannot encode " .. valueType .. " in inputTape JSON")
		end

		local indent = string.rep("  ", depth)
		local childIndent = string.rep("  ", depth + 1)
		if is_array(value) then
			if #value == 0 then
				return "[]"
			end
			local items = {}
			for i = 1, #value do
				items[#items + 1] = childIndent .. json_encode(value[i], depth + 1)
			end
			return "[\n" .. table.concat(items, ",\n") .. "\n" .. indent .. "]"
		end

		local keys = {}
		for key, _ in pairs(value) do
			keys[#keys + 1] = tostring(key)
		end
		table.sort(keys)
		if #keys == 0 then
			return "{}"
		end
		local items = {}
		for i = 1, #keys do
			local key = keys[i]
			items[#items + 1] = childIndent .. json_encode(key, depth + 1) .. ": " .. json_encode(value[key], depth + 1)
		end
		return "{\n" .. table.concat(items, ",\n") .. "\n" .. indent .. "}"
	end

	local function json_decode(text)
		local pos = 1
		local length = #text

		local function decode_error(message)
			error(string.format("inputTape JSON parse error at byte %d: %s", pos, message))
		end

		local function skip_whitespace()
			while pos <= length do
				local char = text:sub(pos, pos)
				if char ~= " " and char ~= "\t" and char ~= "\r" and char ~= "\n" then
					break
				end
				pos = pos + 1
			end
		end

		local parse_value

		local function parse_string()
			if text:sub(pos, pos) ~= "\"" then
				decode_error("expected string")
			end
			pos = pos + 1
			local parts = {}
			while pos <= length do
				local char = text:sub(pos, pos)
				if char == "\"" then
					pos = pos + 1
					return table.concat(parts)
				end
				if char == "\\" then
					pos = pos + 1
					local esc = text:sub(pos, pos)
					if esc == "\"" or esc == "\\" or esc == "/" then
						parts[#parts + 1] = esc
					elseif esc == "b" then
						parts[#parts + 1] = "\b"
					elseif esc == "f" then
						parts[#parts + 1] = "\f"
					elseif esc == "n" then
						parts[#parts + 1] = "\n"
					elseif esc == "r" then
						parts[#parts + 1] = "\r"
					elseif esc == "t" then
						parts[#parts + 1] = "\t"
					elseif esc == "u" then
						local hex = text:sub(pos + 1, pos + 4)
						if #hex ~= 4 or not hex:match("^[%da-fA-F]+$") then
							decode_error("invalid unicode escape")
						end
						local codepoint = tonumber(hex, 16)
						if codepoint < 0x80 then
							parts[#parts + 1] = string.char(codepoint)
						else
							parts[#parts + 1] = "?"
						end
						pos = pos + 4
					else
						decode_error("invalid escape sequence")
					end
				else
					parts[#parts + 1] = char
				end
				pos = pos + 1
			end
			decode_error("unterminated string")
		end

		local function parse_number()
			local start = pos
			local char = text:sub(pos, pos)
			if char == "-" then
				pos = pos + 1
			end
			while text:sub(pos, pos):match("%d") do
				pos = pos + 1
			end
			if text:sub(pos, pos) == "." then
				pos = pos + 1
				while text:sub(pos, pos):match("%d") do
					pos = pos + 1
				end
			end
			char = text:sub(pos, pos)
			if char == "e" or char == "E" then
				pos = pos + 1
				char = text:sub(pos, pos)
				if char == "+" or char == "-" then
					pos = pos + 1
				end
				while text:sub(pos, pos):match("%d") do
					pos = pos + 1
				end
			end
			local numeric = tonumber(text:sub(start, pos - 1))
			if numeric == nil then
				decode_error("invalid number")
			end
			return numeric
		end

		local function parse_array()
			if text:sub(pos, pos) ~= "[" then
				decode_error("expected array")
			end
			pos = pos + 1
			local array = {}
			skip_whitespace()
			if text:sub(pos, pos) == "]" then
				pos = pos + 1
				return array
			end
			while true do
				skip_whitespace()
				array[#array + 1] = parse_value()
				skip_whitespace()
				local char = text:sub(pos, pos)
				if char == "]" then
					pos = pos + 1
					return array
				end
				if char ~= "," then
					decode_error("expected ',' or ']'")
				end
				pos = pos + 1
			end
		end

		local function parse_object()
			if text:sub(pos, pos) ~= "{" then
				decode_error("expected object")
			end
			pos = pos + 1
			local object = {}
			skip_whitespace()
			if text:sub(pos, pos) == "}" then
				pos = pos + 1
				return object
			end
			while true do
				skip_whitespace()
				local key = parse_string()
				skip_whitespace()
				if text:sub(pos, pos) ~= ":" then
					decode_error("expected ':'")
				end
				pos = pos + 1
				skip_whitespace()
				object[key] = parse_value()
				skip_whitespace()
				local char = text:sub(pos, pos)
				if char == "}" then
					pos = pos + 1
					return object
				end
				if char ~= "," then
					decode_error("expected ',' or '}'")
				end
				pos = pos + 1
			end
		end

		function parse_value()
			skip_whitespace()
			local char = text:sub(pos, pos)
			if char == "\"" then
				return parse_string()
			end
			if char == "{" then
				return parse_object()
			end
			if char == "[" then
				return parse_array()
			end
			if char == "-" or char:match("%d") then
				return parse_number()
			end
			if text:sub(pos, pos + 3) == "true" then
				pos = pos + 4
				return true
			end
			if text:sub(pos, pos + 4) == "false" then
				pos = pos + 5
				return false
			end
			if text:sub(pos, pos + 3) == "null" then
				pos = pos + 4
				return nil
			end
			decode_error("unexpected token")
		end

		local decoded = parse_value()
		skip_whitespace()
		if pos <= length then
			decode_error("trailing data")
		end
		return decoded
	end

	local function default_metadata()
		return {
			created_by = "lua-input-tape",
			format_note = DEFAULT_FORMAT_NOTE,
		}
	end

	local function normalize_button_name(name)
		local normalized = tostring(name):gsub("%s+", ""):upper():gsub("-", "_")
		normalized = BUTTON_ALIASES[normalized] or normalized
		if normalized == "NONE" then
			return normalized
		end
		if M.BUTTON_BITS[normalized] == nil then
			error("unknown button name: " .. tostring(name))
		end
		return normalized
	end

	local function parse_mask(value)
		local valueType = type(value)
		if valueType == "number" then
			local numeric = math.floor(value)
			if numeric < 0 or numeric > 0xFFFFFFFF then
				error("mask must fit in uint32")
			end
			return numeric
		end
		if valueType == "string" then
			local textValue = trim(value)
			if textValue == "" then
				return 0
			end
			if textValue:match("^0[xX][%da-fA-F]+$") then
				return tonumber(textValue)
			end
			if textValue:match("^%d+$") then
				return tonumber(textValue, 10)
			end
			return M.maskFromButtons(textValue)
		end
		error("cannot parse key mask from " .. valueType)
	end

	local function normalize_frame_count(value)
		local numeric = tonumber(value)
		if numeric == nil or numeric < 1 or numeric % 1 ~= 0 then
			error("every input tape run must have a positive integer frame count")
		end
		return numeric
	end

	local function canonical_run(run)
		if type(run) ~= "table" then
			error("every input tape run must be a table")
		end

		local mask
		if run.mask ~= nil then
			mask = parse_mask(run.mask)
		elseif run.buttons ~= nil then
			mask = M.maskFromButtons(run.buttons)
		else
			error("input tape run needs either mask or buttons")
		end

		mask = bit_band(mask, GBA_BUTTON_MASK)
		local frames = normalize_frame_count(run.frames)
		return {
			mask = M.formatMask(mask),
			buttons = M.buttonNames(mask),
			frames = frames,
		}
	end

	local function canonical_tape(source, opts)
		if type(source) ~= "table" then
			error("input tape needs a table or run list")
		end

		local runsSource = source.runs or source
		if type(runsSource) ~= "table" or #runsSource == 0 then
			error("input tape has no runs")
		end

		local runs = {}
		local frameCount = 0
		for i = 1, #runsSource do
			local run = canonical_run(runsSource[i])
			runs[i] = run
			frameCount = frameCount + run.frames
		end

		if source.format ~= nil and source.format ~= M.FORMAT then
			error("unsupported input tape format: " .. tostring(source.format))
		end
		if source.frame_count ~= nil and tonumber(source.frame_count) ~= frameCount then
			error(string.format("frame_count mismatch: header=%s actual=%d", tostring(source.frame_count), frameCount))
		end

		local metadata = copy_json_value(option_value(opts, "metadata", source.metadata))
		if type(metadata) ~= "table" then
			metadata = {}
		end
		for key, value in pairs(default_metadata()) do
			if metadata[key] == nil then
				metadata[key] = value
			end
		end

		local startProbe = copy_json_value(option_value(opts, "start_probe", source.start_probe))
		if type(startProbe) ~= "table" then
			startProbe = {}
		end
		local endProbe = copy_json_value(option_value(opts, "end_probe", source.end_probe))
		if type(endProbe) ~= "table" then
			endProbe = {}
		end

		return {
			format = M.FORMAT,
			frame_count = tostring(frameCount),
			button_bits = copy_json_value(M.BUTTON_BITS),
			metadata = metadata,
			start_probe = startProbe,
			end_probe = endProbe,
			runs = runs,
		}
	end

	local function probe_core(emu)
		local probe = {}

		local ok, value = pcall(function()
			return emu:currentFrame()
		end)
		if ok and value ~= nil then
			probe.frame_counter = value
		end

		ok, value = pcall(function()
			return emu:platform()
		end)
		if ok and value ~= nil then
			probe.platform = value
		end

		ok, value = pcall(function()
			return emu:getGameTitle()
		end)
		if ok and value ~= nil then
			probe.game_title = value
		end

		ok, value = pcall(function()
			return emu:getGameCode()
		end)
		if ok and value ~= nil then
			probe.game_code = value
		end

		if emu.read16 ~= nil then
			ok, value = pcall(function()
				return emu:read16(KEYINPUT_ADDR)
			end)
			if ok and value ~= nil then
				local keyinput = bit_band(value, GBA_BUTTON_MASK)
				probe.keyinput = M.formatMask(keyinput)
				probe.held_from_keyinput = M.formatMask(GBA_BUTTON_MASK - keyinput)
			end
		end

		return probe
	end

	local function run_exact_frames(emu, mask, frames)
		if emu.runFramesWithKeys ~= nil then
			emu:runFramesWithKeys(mask, frames)
			return
		end
		emu:setKeys(mask)
		if emu.runFrames ~= nil then
			emu:runFrames(frames)
			return
		end
		for _ = 1, frames do
			emu:runFrame()
		end
	end

	local function compress_frames(frames)
		local runs = {}
		local currentMask = nil
		local currentFrames = 0
		for i = 1, #frames do
			local mask = bit_band(parse_mask(frames[i]), GBA_BUTTON_MASK)
			if currentMask == nil then
				currentMask = mask
				currentFrames = 1
			elseif currentMask == mask then
				currentFrames = currentFrames + 1
			else
				runs[#runs + 1] = {
					mask = currentMask,
					frames = currentFrames,
				}
				currentMask = mask
				currentFrames = 1
			end
		end
		if currentMask ~= nil then
			runs[#runs + 1] = {
				mask = currentMask,
				frames = currentFrames,
			}
		end
		return runs
	end

	function M.formatMask(mask)
		return string.format("0x%08X", parse_mask(mask) % (2 ^ 32))
	end

	function M.maskFromButtons(buttons)
		if type(buttons) == "string" then
			local textValue = trim(buttons)
			if textValue == "" then
				return 0
			end
			if textValue:match("^0[xX][%da-fA-F]+$") then
				return tonumber(textValue)
			end
			if textValue:match("^%d+$") then
				return tonumber(textValue, 10)
			end
			buttons = textValue:gsub("|", "+")
		end

		local parts = {}
		if type(buttons) == "string" then
			for token in buttons:gmatch("[^+]+") do
				parts[#parts + 1] = token
			end
		elseif type(buttons) == "table" then
			for i = 1, #buttons do
				parts[#parts + 1] = buttons[i]
			end
		else
			error("buttons must be a string or table")
		end

		local mask = 0
		for i = 1, #parts do
			local name = normalize_button_name(parts[i])
			if name ~= "NONE" then
				mask = bit_bor(mask, bit_lshift(1, M.BUTTON_BITS[name]))
			end
		end
		return mask
	end

	function M.buttonNames(mask)
		local numeric = bit_band(parse_mask(mask), 0xFFFFFFFF)
		local names = {}
		local known = 0
		for i = 1, #BUTTON_ORDER do
			local name = BUTTON_ORDER[i]
			local bit = bit_lshift(1, M.BUTTON_BITS[name])
			if bit_band(numeric, bit) ~= 0 then
				names[#names + 1] = name
				known = bit_bor(known, bit)
			end
		end
		local unknown = numeric - known
		unknown = bit_band(unknown, 0xFFFFFFFF)
		if unknown ~= 0 then
			names[#names + 1] = M.formatMask(unknown)
		end
		if #names == 0 then
			names[1] = "NONE"
		end
		return names
	end

	function M.fromRuns(runs, opts)
		return canonical_tape(runs, opts)
	end

	function M.fromFrames(frames, opts)
		if type(frames) ~= "table" or #frames == 0 then
			error("input tape needs at least one frame")
		end
		return canonical_tape(compress_frames(frames), opts)
	end

	function M.load(path)
		local handle, openError = io.open(path, "rb")
		if not handle then
			error("could not open input tape for reading: " .. tostring(openError))
		end
		local text = handle:read("*a")
		handle:close()
		return canonical_tape(json_decode(text))
	end

	function M.save(path, tape)
		local normalized = canonical_tape(tape)
		local handle, openError = io.open(path, "wb")
		if not handle then
			error("could not open input tape for writing: " .. tostring(openError))
		end
		handle:write(json_encode(normalized))
		handle:write("\n")
		handle:close()
		return true
	end

	function M.replay(emu, tape, opts)
		local normalized = canonical_tape(tape)
		local clearBefore = option_value(opts, "clear_before", true)
		local clearAfter = option_value(opts, "clear_after", true)
		local verifyFrameCounter = option_value(opts, "verify_frame_counter", true)

		if clearBefore then
			emu:setKeys(0)
		end

		local startProbe = probe_core(emu)
		local startFrame = startProbe.frame_counter

		for i = 1, #normalized.runs do
			local run = normalized.runs[i]
			run_exact_frames(emu, parse_mask(run.mask), run.frames)
		end

		if clearAfter then
			emu:setKeys(0)
		end

		local endProbe = probe_core(emu)
		if verifyFrameCounter and type(startFrame) == "number" and type(endProbe.frame_counter) == "number" then
			local advanced = endProbe.frame_counter - startFrame
			local expected = tonumber(normalized.frame_count)
			if advanced ~= expected then
				error(string.format("input tape replay advanced %d frame(s), expected %d", advanced, expected))
			end
		end

		return {
			frames = tonumber(normalized.frame_count),
			start_probe = startProbe,
			end_probe = endProbe,
		}
	end

	function M.recordPlan(emu, runs, opts)
		local tape = canonical_tape(runs, opts)
		local result = M.replay(emu, tape, opts)
		tape.start_probe = copy_json_value(result.start_probe)
		tape.end_probe = copy_json_value(result.end_probe)
		return tape
	end

	function M.recordCurrentKeys(emu, frames, opts)
		local frameCount = normalize_frame_count(frames)
		local clearBefore = option_value(opts, "clear_before", false)
		local clearAfter = option_value(opts, "clear_after", false)
		if clearBefore then
			emu:setKeys(0)
		end
		local startProbe = probe_core(emu)
		local perFrame = {}
		for i = 1, frameCount do
			perFrame[i] = bit_band(emu:getKeys(), GBA_BUTTON_MASK)
			emu:runFrame()
		end
		if clearAfter then
			emu:setKeys(0)
		end
		local tape = M.fromFrames(perFrame, opts)
		tape.start_probe = startProbe
		tape.end_probe = probe_core(emu)
		return tape
	end

	return M
end)()
