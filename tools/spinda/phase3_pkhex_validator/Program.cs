using System.Buffers.Binary;
using System.Diagnostics;
using System.IO.Compression;
using System.Text.Json;
using System.Text.RegularExpressions;
using PKHeX.Core;

const int ExpectedRecords = 65_536;
const int RecordSize = 80;
const ushort SpindaSpecies = 327;

var config = Config.Parse(args);
if (config.ShowHelp)
{
    Config.PrintHelp();
    return 0;
}

var zipPaths = Directory.EnumerateFiles(config.Root, "0x*.spinda80.zip", SearchOption.TopDirectoryOnly)
    .Where(path => ZipName().IsMatch(Path.GetFileName(path)))
    .OrderBy(static path => path, StringComparer.OrdinalIgnoreCase)
    .ToList();
if (config.LimitZips is > 0 && config.LimitZips < zipPaths.Count)
    zipPaths = zipPaths.Take(config.LimitZips.Value).ToList();

var started = DateTimeOffset.UtcNow;
var results = new List<LaneResult>(zipPaths.Count);
var totalWatch = Stopwatch.StartNew();

for (var index = 0; index < zipPaths.Count; index++)
{
    var result = AuditZip(zipPaths[index], config);
    results.Add(result);
    if (!config.Quiet)
    {
        var state = result.Errors.Count == 0 ? "OK" : "BAD";
        Console.WriteLine(
            $"{index + 1:D3}/{zipPaths.Count:D3} {state} {result.Name} entries={result.EntryCount} " +
            $"pkhex={result.PkhexParsed} sec={result.ElapsedSeconds:F3}");
    }
}

var report = new
{
    root = config.Root,
    pkhex_core = Environment.GetEnvironmentVariable("PKHEX_CORE_DLL") ?? "external PKHeX.Core reference",
    started_utc = started,
    finished_utc = DateTimeOffset.UtcNow,
    elapsed_seconds = Math.Round(totalWatch.Elapsed.TotalSeconds, 3),
    zip_count = results.Count,
    bad_zip_count = results.Count(static r => r.Errors.Count != 0),
    warning_zip_count = results.Count(static r => r.Warnings.Count != 0),
    total_entries_observed = results.Sum(static r => r.EntryCount),
    total_pkhex_parsed = results.Sum(static r => r.PkhexParsed),
    bad = results.Where(static r => r.Errors.Count != 0).Select(static r => new { r.Name, r.Errors }),
    warnings = results.Where(static r => r.Warnings.Count != 0).Select(static r => new { r.Name, r.Warnings }),
    results,
};

Directory.CreateDirectory(Path.GetDirectoryName(config.ReportPath)!);
File.WriteAllText(
    config.ReportPath,
    JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true }) + Environment.NewLine);

Console.WriteLine($"REPORT {config.ReportPath}");
Console.WriteLine(
    $"SUMMARY zips={report.zip_count} bad={report.bad_zip_count} warnings={report.warning_zip_count} " +
    $"entries={report.total_entries_observed} pkhex={report.total_pkhex_parsed} sec={report.elapsed_seconds:F3}");

return report.bad_zip_count == 0 ? 0 : 1;

static LaneResult AuditZip(string path, Config config)
{
    var watch = Stopwatch.StartNew();
    var name = Path.GetFileName(path);
    var match = ZipName().Match(name);
    var lane = match.Success ? Convert.ToInt32(match.Groups[1].Value, 16) : -1;
    var result = new LaneResult
    {
        Zip = path,
        Name = name,
        Lane = lane >= 0 ? $"0x{lane:X4}" : null,
        ZipSize = new FileInfo(path).Length,
    };

    if (!match.Success)
    {
        result.Errors.Add("bad_zip_name");
        result.ElapsedSeconds = watch.Elapsed.TotalSeconds;
        return result;
    }

    var seenUpper = new byte[ExpectedRecords / 8];
    var names = new HashSet<string>(StringComparer.Ordinal);
    var buffer = new byte[RecordSize];

    try
    {
        using var archive = ZipFile.OpenRead(path);
        result.EntryCount = archive.Entries.Count;
        if (archive.Entries.Count != ExpectedRecords)
            result.Errors.Add($"entry_count:{archive.Entries.Count}");

        foreach (var entry in archive.Entries)
        {
            if (!names.Add(entry.FullName))
                AddSample(result, "duplicate_names", entry.FullName, config.SampleLimit);

            var entryMatch = EntryName().Match(entry.FullName);
            if (!entryMatch.Success)
            {
                AddSample(result, "bad_names", entry.FullName, config.SampleLimit);
                continue;
            }

            var pid = Convert.ToUInt32(entryMatch.Groups[1].Value, 16);
            var upper = (int)(pid >> 16);
            var lower = (int)(pid & 0xFFFF);
            if (lower != lane)
                AddSample(result, "bad_lower", entry.FullName, config.SampleLimit);
            if (GetBit(seenUpper, upper))
                AddSample(result, "duplicate_upper", $"0x{upper:X4}", config.SampleLimit);
            else
                SetBit(seenUpper, upper);

            if (entry.Length != RecordSize)
            {
                AddSample(result, "bad_sizes", $"{entry.FullName}:{entry.Length}", config.SampleLimit);
                continue;
            }

            try
            {
                if (!ReadExactEntry(entry, buffer))
                {
                    AddSample(result, "read_errors", $"{entry.FullName}:short_read", config.SampleLimit);
                    continue;
                }
            }
            catch (Exception ex) when (ex is InvalidDataException or IOException)
            {
                AddSample(result, "read_errors", $"{entry.FullName}:{ex.Message}", config.SampleLimit);
                continue;
            }

            var rawPid = BinaryPrimitives.ReadUInt32LittleEndian(buffer);
            if (rawPid != pid)
                AddSample(result, "bad_content_pid", entry.FullName, config.SampleLimit);

            if (!ValidatePkhex(buffer, pid, config.ExpectedState, out var pkhexError, out var usedForcedDecrypt))
                AddSample(result, "pkhex_errors", $"{entry.FullName}:{pkhexError}", config.SampleLimit);
            else
            {
                result.PkhexParsed++;
                if (usedForcedDecrypt)
                    result.PkhexForcedDecrypt++;
            }
        }
    }
    catch (Exception ex) when (ex is InvalidDataException or IOException)
    {
        result.Errors.Add($"bad_zip:{ex.Message}");
    }

    if (!AllBitsSet(seenUpper))
    {
        result.Errors.Add("upper_coverage_incomplete");
        result.MissingUpperSample = MissingUpperSample(seenUpper, config.SampleLimit);
    }

    foreach (var (key, count) in result.Counters.OrderBy(static pair => pair.Key, StringComparer.Ordinal))
    {
        if (count != 0)
            result.Errors.Add($"{key}:{count}");
    }

    result.ElapsedSeconds = watch.Elapsed.TotalSeconds;
    return result;
}

static bool ValidatePkhex(byte[] record, uint expectedPid, ExpectedPk3State expectedState, out string error, out bool usedForcedDecrypt)
{
    // PKHeX's normal PK3 constructor auto-detects encrypted Gen 3 data by
    // comparing the raw block checksum. Rare encrypted records collide with
    // that checksum and look "already decrypted"; forced decrypt recovers them.
    if (TryValidatePkhex((byte[])record.Clone(), expectedPid, expectedState, out error))
    {
        usedForcedDecrypt = false;
        return true;
    }

    try
    {
        var decrypted = PokeCrypto.DecryptArray3((byte[])record.Clone());
        if (TryValidatePkhex(decrypted, expectedPid, expectedState, out error))
        {
            usedForcedDecrypt = true;
            return true;
        }
    }
    catch (Exception ex)
    {
        error = ex.GetType().Name + ":" + ex.Message;
    }

    usedForcedDecrypt = false;
    return false;
}

static bool TryValidatePkhex(byte[] data, uint expectedPid, ExpectedPk3State expectedState, out string error)
{
    try
    {
        // PKHeX mutates/decrypts the supplied memory and pads 80-byte stored PK3
        // records to party size internally. Feed caller-owned data, never ZIP buffer.
        var pk = new PK3(data);
        return ValidatePk3(pk, expectedPid, expectedState, out error);
    }
    catch (Exception ex)
    {
        error = ex.GetType().Name + ":" + ex.Message;
        return false;
    }
}

static bool ValidatePk3(PK3 pk, uint expectedPid, ExpectedPk3State expectedState, out string error)
{
    if (pk.Context != EntityContext.Gen3 || pk.Format != 3)
    {
        error = $"not_gen3:{pk.Context}/{pk.Format}";
        return false;
    }
    if (pk.PID != expectedPid)
    {
        error = $"pid:0x{pk.PID:X8}";
        return false;
    }
    if (!pk.ChecksumValid)
    {
        error = "checksum";
        return false;
    }
    if (pk.Species != SpindaSpecies)
    {
        error = $"species:{pk.Species}";
        return false;
    }
    switch (expectedState)
    {
        case ExpectedPk3State.Egg:
            if (!pk.IsEgg)
            {
                error = "not_egg";
                return false;
            }
            break;
        case ExpectedPk3State.HatchedShiny:
            if (pk.IsEgg)
            {
                error = "still_egg";
                return false;
            }
            if (!pk.IsShiny)
            {
                error = "not_shiny";
                return false;
            }
            break;
        case ExpectedPk3State.HatchedNotShiny:
            if (pk.IsEgg)
            {
                error = "still_egg";
                return false;
            }
            if (pk.IsShiny)
            {
                error = "unexpected_shiny";
                return false;
            }
            break;
    }
    error = "";
    return true;
}

static bool ReadExactEntry(ZipArchiveEntry entry, byte[] buffer)
{
    Array.Clear(buffer);
    using var stream = entry.Open();
    var offset = 0;
    while (offset < buffer.Length)
    {
        var read = stream.Read(buffer, offset, buffer.Length - offset);
        if (read == 0)
            return false;
        offset += read;
    }
    return stream.ReadByte() == -1;
}

static void Count(Dictionary<string, int> counters, string key) =>
    counters[key] = counters.GetValueOrDefault(key) + 1;

static bool GetBit(byte[] bits, int index) => (bits[index >> 3] & (1 << (index & 7))) != 0;

static void SetBit(byte[] bits, int index) => bits[index >> 3] |= (byte)(1 << (index & 7));

static bool AllBitsSet(byte[] bits) => bits.All(static value => value == 0xFF);

static List<string> MissingUpperSample(byte[] seenUpper, int limit)
{
    var missing = new List<string>(Math.Min(limit, 32));
    for (var upper = 0; upper < ExpectedRecords && missing.Count < limit; upper++)
    {
        if (!GetBit(seenUpper, upper))
            missing.Add($"0x{upper:X4}");
    }
    return missing;
}

static void AddSample(LaneResult result, string key, string sample, int limit)
{
    Count(result.Counters, key);
    if (!result.Samples.TryGetValue(key, out var samples))
        result.Samples[key] = samples = [];
    if (samples.Count < limit)
        samples.Add(sample);
}

partial class Program
{
    [GeneratedRegex(@"^0x([0-9A-Fa-f]{4})\.spinda80\.zip$", RegexOptions.CultureInvariant)]
    private static partial Regex ZipName();

    [GeneratedRegex(@"^0x([0-9A-Fa-f]{8})\.pk3$", RegexOptions.CultureInvariant)]
    private static partial Regex EntryName();
}

sealed class Config
{
    public string Root { get; init; } = Path.GetFullPath("Phase3SpindaBlocks");
    public string ReportPath { get; init; } =
        Path.Combine(Path.GetFullPath("Phase3SpindaBlocks"), $"_phase3_pkhex_audit_{DateTime.Now:yyyyMMdd_HHmmss}.json");
    public int SampleLimit { get; init; } = 32;
    public int? LimitZips { get; init; }
    public ExpectedPk3State ExpectedState { get; init; } = ExpectedPk3State.Egg;
    public bool Quiet { get; init; }
    public bool ShowHelp { get; init; }

    public static Config Parse(string[] args)
    {
        var root = Path.GetFullPath("Phase3SpindaBlocks");
        string? report = null;
        int sampleLimit = 32;
        int? limitZips = null;
        var expectedState = ExpectedPk3State.Egg;
        var quiet = false;
        var showHelp = false;

        for (var i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--root":
                    root = RequireValue(args, ref i);
                    break;
                case "--report":
                    report = RequireValue(args, ref i);
                    break;
                case "--sample-limit":
                    sampleLimit = int.Parse(RequireValue(args, ref i));
                    break;
                case "--limit-zips":
                    limitZips = int.Parse(RequireValue(args, ref i));
                    break;
                case "--expected-state":
                    expectedState = ParseExpectedState(RequireValue(args, ref i));
                    break;
                case "--quiet":
                    quiet = true;
                    break;
                case "--help":
                case "-h":
                case "/?":
                    showHelp = true;
                    break;
                default:
                    throw new ArgumentException($"Unknown argument: {args[i]}");
            }
        }

        if (sampleLimit < 1)
            throw new ArgumentOutOfRangeException(nameof(sampleLimit), "--sample-limit must be positive");

        return new Config
        {
            Root = root,
            ReportPath = report ?? Path.Combine(root, $"_phase3_pkhex_audit_{DateTime.Now:yyyyMMdd_HHmmss}.json"),
            SampleLimit = sampleLimit,
            LimitZips = limitZips,
            ExpectedState = expectedState,
            Quiet = quiet,
            ShowHelp = showHelp,
        };
    }

    public static void PrintHelp()
    {
        Console.WriteLine("""
        Phase3PkhexValidator

        Read-only PKHeX.Core validator for Phase 3 Spinda ZIPs.
        ZIP entries are decompressed into RAM only; no loose PK3 files are written.

        Options:
          --root PATH          Folder with 0x####.spinda80.zip files.
          --report PATH        JSON report path.
          --limit-zips N       Check first N lane ZIPs, for proof runs.
          --expected-state S    egg, hatched-shiny, or hatched-not-shiny. Default: egg.
          --sample-limit N     Samples per error bucket. Default: 32.
          --quiet              Suppress per-lane progress.
        """);
    }

    private static string RequireValue(string[] args, ref int index)
    {
        if (index + 1 >= args.Length)
            throw new ArgumentException($"Missing value for {args[index]}");
        return args[++index];
    }

    private static ExpectedPk3State ParseExpectedState(string text) => text switch
    {
        "egg" => ExpectedPk3State.Egg,
        "hatched-shiny" or "shiny" => ExpectedPk3State.HatchedShiny,
        "hatched-not-shiny" or "not-shiny" or "non-shiny" => ExpectedPk3State.HatchedNotShiny,
        _ => throw new ArgumentException($"Unknown expected state: {text}"),
    };
}

enum ExpectedPk3State
{
    Egg,
    HatchedShiny,
    HatchedNotShiny,
}

sealed class LaneResult
{
    public required string Zip { get; init; }
    public required string Name { get; init; }
    public string? Lane { get; init; }
    public long ZipSize { get; init; }
    public int EntryCount { get; set; }
    public int PkhexParsed { get; set; }
    public int PkhexForcedDecrypt { get; set; }
    public double ElapsedSeconds { get; set; }
    public Dictionary<string, int> Counters { get; } = new(StringComparer.Ordinal);
    public Dictionary<string, List<string>> Samples { get; } = new(StringComparer.Ordinal);
    public List<string> MissingUpperSample { get; set; } = [];
    public List<string> Errors { get; } = [];
    public List<string> Warnings { get; } = [];
}

