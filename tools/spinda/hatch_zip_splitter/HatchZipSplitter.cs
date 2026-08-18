using System.Diagnostics;
using System.Buffers.Binary;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;
using PKHeX.Core;

namespace SpindaHatchZipSplitter;

public static class HatchZipSplitter
{
    public const int ShinyValueCount = 8192;
    private const int StoredPk3Size = 80;
    internal static readonly Regex SaveNamePattern = new(
        @"^TSV-(?<tsv>\d{4})-sid-(?<sid>\d{5})\.sav$",
        RegexOptions.Compiled | RegexOptions.CultureInvariant | RegexOptions.IgnoreCase);

    public static SplitResult Run(SplitterConfig config)
    {
        config.Validate();
        var stopwatch = Stopwatch.StartNew();
        var result = new SplitResult
        {
            StartedUtc = DateTimeOffset.UtcNow,
            SaveDirectory = config.SaveDirectory,
            InputDirectory = config.InputDirectory,
            ShinyOutputPath = config.ShinyOutputPath,
            NotShinyOutputPath = config.NotShinyOutputPath,
            SampleLimit = config.SampleLimit,
        };

        var inputZips = ResolveInputZips(config);
        result.InputZipCount = inputZips.Count;
        result.InputZips.AddRange(inputZips);

        var saveIndex = TsvSaveIndex.Load(config, result);
        result.SaveContextsLoaded = saveIndex.Count;
        result.SaveBankComplete = saveIndex.Count == ShinyValueCount;

        if (result.HardIssueCount != 0)
        {
            FinalizeReportOnly(config, result, stopwatch);
            return result;
        }

        PrepareOutputPaths(config);
        var shinyTemp = GetTempPath(config.ShinyOutputPath);
        var notShinyTemp = GetTempPath(config.NotShinyOutputPath);

        try
        {
            using (var shinyZip = CreateOutputWriter(shinyTemp, config))
            using (var notShinyZip = CreateOutputWriter(notShinyTemp, config))
            {
                ProcessInputs(inputZips, saveIndex, shinyZip, notShinyZip, config, result);
                result.FinishedUtc = DateTimeOffset.UtcNow;
                result.ElapsedSeconds = Math.Round(stopwatch.Elapsed.TotalSeconds, 3);
                if (config.IncludeManifest)
                {
                    WriteManifestEntry(shinyZip, result, "shiny");
                    WriteManifestEntry(notShinyZip, result, "not_shiny");
                }
            }

            if (result.HardIssueCount != 0 && !config.SkipBadRecords)
            {
                DeleteIfExists(shinyTemp);
                DeleteIfExists(notShinyTemp);
                FinalizeReportOnly(config, result, stopwatch);
                return result;
            }

            MoveTempIntoPlace(shinyTemp, config.ShinyOutputPath, config.Overwrite);
            MoveTempIntoPlace(notShinyTemp, config.NotShinyOutputPath, config.Overwrite);
        }
        catch
        {
            DeleteIfExists(shinyTemp);
            DeleteIfExists(notShinyTemp);
            throw;
        }

        WriteReport(config, result);
        return result;
    }

    public static TrainerIndexWriteResult WriteTrainerIndex(SplitterConfig config)
    {
        config.Validate();
        if (string.IsNullOrWhiteSpace(config.TrainerIndexOutputPath))
            throw new ArgumentException("--trainer-index requires an output path.");

        var result = new SplitResult
        {
            StartedUtc = DateTimeOffset.UtcNow,
            SaveDirectory = config.SaveDirectory,
            SampleLimit = config.SampleLimit,
        };
        var index = TsvSaveIndex.Load(config, result);
        result.SaveContextsLoaded = index.Count;
        result.SaveBankComplete = index.Count == ShinyValueCount;
        result.FinishedUtc = DateTimeOffset.UtcNow;

        var entries = index.Contexts
            .OrderBy(static context => context.Tsv)
            .Select(static context => TrainerIndexEntry.FromContext(context))
            .ToList();
        var document = new TrainerIndexDocument
        {
            GeneratedUtc = DateTimeOffset.UtcNow,
            SaveDirectory = Path.GetFullPath(config.SaveDirectory),
            ExpectedEntries = ShinyValueCount,
            EntryCount = entries.Count,
            Complete = entries.Count == ShinyValueCount && result.HardIssueCount == 0,
            TrainerId = config.TrainerId,
            HardIssueCount = result.HardIssueCount,
            SoftIssueCount = result.SoftIssueCount,
            IssueCounts = result.IssueCounts,
            Issues = result.Issues,
            Entries = entries,
        };

        var outputPath = Path.GetFullPath(config.TrainerIndexOutputPath);
        Directory.CreateDirectory(Path.GetDirectoryName(outputPath)!);
        File.WriteAllText(outputPath, JsonSerializer.Serialize(document, JsonOptions) + Environment.NewLine, Encoding.UTF8);
        return new TrainerIndexWriteResult(outputPath, entries.Count, document.Complete, result.HardIssueCount, result.SoftIssueCount);
    }

    public static int ComputePokemonShinyValue(uint pid) => (int)(((pid & 0xFFFF) ^ (pid >> 16)) >> 3);

    public static bool IsShinyFor(uint pid, int trainerId, int secretId)
    {
        var id32 = (uint)((trainerId & 0xFFFF) | ((secretId & 0xFFFF) << 16));
        return ShinyUtil.GetIsShiny3(id32, pid);
    }

    private static void ProcessInputs(
        IReadOnlyList<string> inputZips,
        TsvSaveIndex saveIndex,
        IHatchZipWriter shinyZip,
        IHatchZipWriter notShinyZip,
        SplitterConfig config,
        SplitResult result)
    {
        var buffer = new byte[StoredPk3Size];

        foreach (var zipPath in inputZips)
        {
            using var sourceZip = ZipFile.OpenRead(zipPath);
            var zipFileName = Path.GetFileName(zipPath);
            result.SourceZipsVisited++;

            foreach (var entry in sourceZip.Entries)
            {
                if (config.LimitEntries is { } limit && result.ProcessedEntries >= limit)
                    return;
                if (entry.FullName.EndsWith("/", StringComparison.Ordinal))
                    continue;

                var source = new EntrySource(zipFileName, entry.FullName);
                if (!entry.FullName.EndsWith(".pk3", StringComparison.OrdinalIgnoreCase))
                {
                    result.AddIssue("bad_entry_extension", source.ToString(), hard: !config.SkipBadRecords);
                    if (ShouldStopAfterIssue(config, result))
                        return;
                    continue;
                }
                if (entry.Length != StoredPk3Size)
                {
                    result.AddIssue("bad_entry_size", $"{source}:{entry.Length}", hard: !config.SkipBadRecords);
                    if (ShouldStopAfterIssue(config, result))
                        return;
                    continue;
                }
                if (!ReadExactEntry(entry, buffer))
                {
                    result.AddIssue("short_entry_read", source.ToString(), hard: !config.SkipBadRecords);
                    if (ShouldStopAfterIssue(config, result))
                        return;
                    continue;
                }
                if (!TryParsePk3(buffer, out var egg, out var forcedDecrypt, out var parseError))
                {
                    result.AddIssue("pkhex_parse_failed", $"{source}:{parseError}", hard: !config.SkipBadRecords);
                    if (ShouldStopAfterIssue(config, result))
                        return;
                    continue;
                }

                if (forcedDecrypt)
                    result.ForcedDecryptCount++;

                if (!ValidateSourceEgg(egg, entry.FullName, source, config, result))
                {
                    if (ShouldStopAfterIssue(config, result))
                        return;
                    continue;
                }

                var psv = ComputePokemonShinyValue(egg.PID);
                if (!saveIndex.TryGet(psv, out var shinyContext))
                {
                    result.AddIssue("missing_matching_tsv_save", $"psv={psv:D4} source={source}", hard: true);
                    if (ShouldStopAfterIssue(config, result))
                        return;
                    continue;
                }
                if (!saveIndex.TryGetNonMatching(psv, out var nonShinyContext))
                {
                    result.AddIssue("missing_nonmatching_tsv_save", $"psv={psv:D4} source={source}", hard: true);
                    if (ShouldStopAfterIssue(config, result))
                        return;
                    continue;
                }

                var shiny = CreateHatchedCopy(egg, shinyContext, config);
                var nonShiny = CreateHatchedCopy(egg, nonShinyContext, config);

                if (!VerifyConverted(shiny, shinyContext, expectedShiny: true, source, result))
                {
                    if (ShouldStopAfterIssue(config, result))
                        return;
                    continue;
                }
                if (!VerifyConverted(nonShiny, nonShinyContext, expectedShiny: false, source, result))
                {
                    if (ShouldStopAfterIssue(config, result))
                        return;
                    continue;
                }

                shinyZip.WritePk3(entry.FullName, shiny);
                notShinyZip.WritePk3(entry.FullName, nonShiny);

                result.ProcessedEntries++;
                result.ShinyWritten++;
                result.NotShinyWritten++;
                result.PsvCounts[psv]++;
                result.ShinySaveUseCounts[shinyContext.Tsv]++;
                result.NonShinySaveUseCounts[nonShinyContext.Tsv]++;
                if (result.ShouldAddSample)
                {
                    result.AddSample(new ConversionSample
                    {
                        Source = source.ToString(),
                        EntryName = entry.FullName,
                        Pid = $"0x{egg.PID:X8}",
                        Psv = psv,
                        ShinyTsv = shinyContext.Tsv,
                        ShinySid = shinyContext.SecretId,
                        NonShinyTsv = nonShinyContext.Tsv,
                        NonShinySid = nonShinyContext.SecretId,
                    });
                }
            }
        }
    }

    private static bool ShouldStopAfterIssue(SplitterConfig config, SplitResult result) =>
        result.HardIssueCount != 0 && !config.SkipBadRecords;

    private static bool ValidateSourceEgg(PK3 egg, string entryName, EntrySource source, SplitterConfig config, SplitResult result)
    {
        var ok = true;
        if (!egg.ChecksumValid)
        {
            result.AddIssue("source_checksum_invalid", source.ToString(), hard: !config.SkipBadRecords);
            ok = false;
        }
        if (egg.Species != config.ExpectedSpecies)
        {
            result.AddIssue("source_species_mismatch", $"{source}:species={egg.Species}", hard: !config.SkipBadRecords);
            ok = false;
        }
        if (!egg.IsEgg && !config.AllowAlreadyHatched)
        {
            result.AddIssue("source_not_egg", source.ToString(), hard: !config.SkipBadRecords);
            ok = false;
        }

        if (TryParsePidEntryName(entryName, out var expectedPid))
        {
            if (egg.PID != expectedPid)
            {
                result.AddIssue("entry_pid_mismatch", $"{source}:pkhex=0x{egg.PID:X8}", hard: !config.SkipBadRecords);
                ok = false;
            }
        }
        else
        {
            result.AddIssue("entry_name_not_pid", source.ToString(), hard: false);
        }

        return ok;
    }

    private static bool TryParsePidEntryName(string entryName, out uint pid)
    {
        // This runs once per PK3 entry in corpus-scale runs. Avoid Regex here.
        var slash = entryName.LastIndexOf('/');
        var backslash = entryName.LastIndexOf('\\');
        var start = Math.Max(slash, backslash) + 1;
        var name = entryName.AsSpan(start);
        pid = 0;

        if (name.Length != 14 ||
            name[0] != '0' ||
            (name[1] | 0x20) != 'x' ||
            name[10] != '.' ||
            (name[11] | 0x20) != 'p' ||
            (name[12] | 0x20) != 'k' ||
            name[13] != '3')
        {
            return false;
        }

        for (var i = 2; i < 10; i++)
        {
            var value = HexValue(name[i]);
            if (value < 0)
                return false;
            pid = (pid << 4) | (uint)value;
        }
        return true;
    }

    private static int HexValue(char value)
    {
        if (value is >= '0' and <= '9')
            return value - '0';
        value = (char)(value | 0x20);
        if (value is >= 'a' and <= 'f')
            return value - 'a' + 10;
        return -1;
    }

    private static PK3 CreateHatchedCopy(PK3 source, TsvSaveContext context, SplitterConfig config)
    {
        var pk = source.Clone();
        var trainer = context.ToTrainerInfo();
        trainer.ApplyTo(pk);
        pk.CurrentLevel = (byte)config.HatchLevel;
        pk.ForceHatchPKM(trainer, reHatch: true);
        pk.RefreshChecksum();
        return pk;
    }

    private static bool VerifyConverted(PK3 pk, TsvSaveContext context, bool expectedShiny, EntrySource source, SplitResult result)
    {
        var expectedTsv = (context.TrainerId ^ context.SecretId) >> 3;
        var isShiny = IsShinyFor(pk.PID, context.TrainerId, context.SecretId);
        var hard = false;

        if (pk.IsEgg)
        {
            result.AddIssue("converted_still_egg", source.ToString(), hard: true);
            hard = true;
        }
        if (!pk.ChecksumValid)
        {
            result.AddIssue("converted_checksum_invalid", source.ToString(), hard: true);
            hard = true;
        }
        if (pk.TID16 != context.TrainerId || pk.SID16 != context.SecretId)
        {
            result.AddIssue("converted_trainer_mismatch", source.ToString(), hard: true);
            hard = true;
        }
        if (expectedTsv != context.Tsv)
        {
            result.AddIssue("context_tsv_mismatch", $"{source}:tsv={context.Tsv:D4}:expected={expectedTsv:D4}", hard: true);
            hard = true;
        }
        if (pk.IsShiny != expectedShiny || isShiny != expectedShiny)
        {
            result.AddIssue("converted_shiny_mismatch", $"{source}:expected={expectedShiny}:pkhex={pk.IsShiny}:formula={isShiny}", hard: true);
            hard = true;
        }

        return !hard;
    }

    private static bool TryParsePk3(byte[] source, out PK3 pk, out bool forcedDecrypt, out string error)
    {
        if (TryParsePk3Direct((byte[])source.Clone(), out pk!, out error))
        {
            forcedDecrypt = false;
            return true;
        }

        try
        {
            var decrypted = (byte[])source.Clone();
            PokeCrypto.Decrypt3(decrypted);
            if (TryParsePk3Direct(decrypted, out pk!, out error))
            {
                forcedDecrypt = true;
                return true;
            }
        }
        catch (Exception ex) when (ex is ArgumentException or InvalidOperationException)
        {
            error = $"{ex.GetType().Name}:{ex.Message}";
        }

        pk = null!;
        forcedDecrypt = false;
        return false;
    }

    private static bool TryParsePk3Direct(byte[] data, out PK3 pk, out string error)
    {
        try
        {
            // PKHeX may decrypt/mutate the input memory. Use caller-owned data.
            pk = new PK3(data);
            if (pk.Context != EntityContext.Gen3 || pk.Format != 3)
            {
                error = $"not_gen3:{pk.Context}/{pk.Format}";
                return false;
            }
            if (!pk.ChecksumValid)
            {
                error = "checksum";
                return false;
            }
            error = "";
            return true;
        }
        catch (Exception ex) when (ex is ArgumentException or InvalidOperationException)
        {
            pk = null!;
            error = $"{ex.GetType().Name}:{ex.Message}";
            return false;
        }
    }

    private static IHatchZipWriter CreateOutputWriter(string path, SplitterConfig config) =>
        config.Compress
            ? new CompressedZipWriter(path)
            : new StoredHatchZipWriter(path);

    private static bool ReadExactEntry(ZipArchiveEntry entry, byte[] buffer)
    {
        using var stream = entry.Open();
        var offset = 0;
        while (offset < buffer.Length)
        {
            var read = stream.Read(buffer, offset, buffer.Length - offset);
            if (read == 0)
                return false;
            offset += read;
        }
        return true;
    }

    private static List<string> ResolveInputZips(SplitterConfig config)
    {
        var paths = new List<string>();
        paths.AddRange(config.InputZips);
        if (!string.IsNullOrWhiteSpace(config.InputDirectory) && Directory.Exists(config.InputDirectory))
        {
            paths.AddRange(Directory.EnumerateFiles(config.InputDirectory, config.InputPattern, SearchOption.TopDirectoryOnly));
        }

        paths = paths
            .Select(Path.GetFullPath)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .Where(path => !IsOutputPath(path, config))
            .OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
            .ToList();
        if (paths.Count == 0)
            throw new ArgumentException("No input ZIPs found. Use --input-dir or --input-zip.");
        foreach (var path in paths)
        {
            if (!File.Exists(path))
                throw new FileNotFoundException("Input ZIP does not exist.", path);
        }
        return paths;
    }

    private static bool IsOutputPath(string path, SplitterConfig config) =>
        path.Equals(Path.GetFullPath(config.ShinyOutputPath), StringComparison.OrdinalIgnoreCase) ||
        path.Equals(Path.GetFullPath(config.NotShinyOutputPath), StringComparison.OrdinalIgnoreCase);

    private static void PrepareOutputPaths(SplitterConfig config)
    {
        if (Path.GetFullPath(config.ShinyOutputPath).Equals(Path.GetFullPath(config.NotShinyOutputPath), StringComparison.OrdinalIgnoreCase))
            throw new ArgumentException("Shiny and not-shiny output paths must be different.");
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(config.ShinyOutputPath))!);
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(config.NotShinyOutputPath))!);
        if (!config.Overwrite)
        {
            if (File.Exists(config.ShinyOutputPath))
                throw new IOException($"Output exists; pass --overwrite: {config.ShinyOutputPath}");
            if (File.Exists(config.NotShinyOutputPath))
                throw new IOException($"Output exists; pass --overwrite: {config.NotShinyOutputPath}");
        }
    }

    private static string GetTempPath(string finalPath) =>
        $"{Path.GetFullPath(finalPath)}.tmp-{Guid.NewGuid():N}";

    private static void MoveTempIntoPlace(string tempPath, string finalPath, bool overwrite)
    {
        if (overwrite)
            File.Move(tempPath, finalPath, overwrite: true);
        else
            File.Move(tempPath, finalPath);
    }

    private static void DeleteIfExists(string path)
    {
        if (File.Exists(path))
            File.Delete(path);
    }

    private static void FinalizeReportOnly(SplitterConfig config, SplitResult result, Stopwatch stopwatch)
    {
        result.FinishedUtc = DateTimeOffset.UtcNow;
        result.ElapsedSeconds = Math.Round(stopwatch.Elapsed.TotalSeconds, 3);
        WriteReport(config, result);
    }

    private static void WriteManifestEntry(IHatchZipWriter archive, SplitResult result, string outputKind)
    {
        archive.WriteJson("_spinda_hatch_manifest.json", new
        {
            output_kind = outputKind,
            result.StartedUtc,
            result.FinishedUtc,
            result.ElapsedSeconds,
            result.InputZipCount,
            result.ProcessedEntries,
            result.SaveContextsLoaded,
            result.SaveBankComplete,
            result.ShinyWritten,
            result.NotShinyWritten,
            result.ForcedDecryptCount,
            result.Samples,
            issue_count = result.TotalIssueCount,
            sampled_issue_count = result.Issues.Count,
            result.HardIssueCount,
            result.SoftIssueCount,
            result.IssueCounts,
        });
    }

    private static void WriteReport(SplitterConfig config, SplitResult result)
    {
        var reportPath = config.ReportPath;
        if (string.IsNullOrWhiteSpace(reportPath))
            reportPath = Path.Combine(Path.GetDirectoryName(Path.GetFullPath(config.ShinyOutputPath))!, $"_spinda_hatch_zip_splitter_{DateTime.Now:yyyyMMdd_HHmmss}.json");
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(reportPath))!);
        result.ReportPath = reportPath;
        File.WriteAllText(reportPath, JsonSerializer.Serialize(result, JsonOptions) + Environment.NewLine);
    }

    internal static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = true,
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        Converters = { new JsonStringEnumConverter(JsonNamingPolicy.SnakeCaseLower) },
    };
}

internal interface IHatchZipWriter : IDisposable
{
    void WritePk3(string entryName, PK3 pk);
    void WriteJson(string entryName, object payload);
}

internal sealed class CompressedZipWriter : IHatchZipWriter
{
    private readonly FileStream stream;
    private readonly ZipArchive archive;

    public CompressedZipWriter(string path)
    {
        stream = new FileStream(path, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None, 1 << 20);
        archive = new ZipArchive(stream, ZipArchiveMode.Create, leaveOpen: false);
    }

    public void WritePk3(string entryName, PK3 pk) =>
        HatchZipSplitterPrivate.WritePk3Entry(archive, entryName, pk, CompressionLevel.Fastest);

    public void WriteJson(string entryName, object payload)
    {
        var entry = archive.CreateEntry(entryName, CompressionLevel.Fastest);
        using var entryStream = entry.Open();
        JsonSerializer.Serialize(entryStream, payload, HatchZipSplitter.JsonOptions);
    }

    public void Dispose()
    {
        archive.Dispose();
        stream.Dispose();
    }
}

internal sealed class StoredHatchZipWriter : IHatchZipWriter
{
    private readonly StoredZipWriter writer;

    public StoredHatchZipWriter(string path) => writer = new StoredZipWriter(path);

    public void WritePk3(string entryName, PK3 pk)
    {
        Span<byte> output = stackalloc byte[80];
        pk.WriteEncryptedDataStored(output);
        writer.AddEntry(entryName, output);
    }

    public void WriteJson(string entryName, object payload)
    {
        var data = JsonSerializer.SerializeToUtf8Bytes(payload, HatchZipSplitter.JsonOptions);
        writer.AddEntry(entryName, data);
    }

    public void Dispose() => writer.Dispose();
}

internal static class HatchZipSplitterPrivate
{
    public static void WritePk3Entry(ZipArchive archive, string entryName, PK3 pk, CompressionLevel compression)
    {
        var entry = archive.CreateEntry(entryName, compression);
        Span<byte> output = stackalloc byte[80];
        pk.WriteEncryptedDataStored(output);
        using var stream = entry.Open();
        stream.Write(output);
    }
}

internal sealed class StoredZipWriter : IDisposable
{
    private const uint LocalFileHeaderSignature = 0x04034B50;
    private const uint CentralDirectorySignature = 0x02014B50;
    private const uint Zip64EndOfCentralDirectorySignature = 0x06064B50;
    private const uint Zip64EndOfCentralDirectoryLocatorSignature = 0x07064B50;
    private const uint EndOfCentralDirectorySignature = 0x06054B50;
    private const ushort VersionNeededDefault = 20;
    private const ushort VersionNeededZip64 = 45;
    private const ushort Utf8Flag = 0x0800;
    private const ushort StoredMethod = 0;
    private const ushort Zip64ExtraId = 0x0001;

    private readonly FileStream output;
    private readonly FileStream centralDirectory;
    private readonly string centralDirectoryPath;
    private bool finished;
    private long entryCount;

    public StoredZipWriter(string path)
    {
        output = new FileStream(path, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None, 1 << 20);
        centralDirectoryPath = $"{path}.central-{Guid.NewGuid():N}.tmp";
        centralDirectory = new FileStream(centralDirectoryPath, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None, 1 << 20);
    }

    public void AddEntry(string entryName, ReadOnlySpan<byte> data)
    {
        if (finished)
            throw new InvalidOperationException("ZIP writer is already finished.");

        var normalizedName = entryName.Replace('\\', '/');
        var nameBytes = Encoding.UTF8.GetBytes(normalizedName);
        if (nameBytes.Length > ushort.MaxValue)
            throw new InvalidDataException($"ZIP entry name is too long: {entryName}");

        var crc = Crc32.Compute(data);
        var localOffset = output.Position;
        WriteLocalHeader(output, nameBytes, crc, data.Length);
        output.Write(data);
        WriteCentralHeader(centralDirectory, nameBytes, crc, data.Length, localOffset);
        entryCount++;
    }

    public void Dispose()
    {
        try
        {
            if (!finished)
                Finish();
        }
        finally
        {
            output.Dispose();
            centralDirectory.Dispose();
            if (File.Exists(centralDirectoryPath))
                File.Delete(centralDirectoryPath);
        }
    }

    private void Finish()
    {
        finished = true;
        var centralOffset = output.Position;
        centralDirectory.Flush();
        var centralSize = centralDirectory.Length;
        centralDirectory.Position = 0;
        centralDirectory.CopyTo(output);

        var needsZip64 = entryCount >= ushort.MaxValue || centralOffset >= uint.MaxValue || centralSize >= uint.MaxValue;
        if (needsZip64)
            WriteZip64EndRecords(output, entryCount, centralSize, centralOffset);
        WriteEndRecord(output, entryCount, centralSize, centralOffset, needsZip64);
        output.Flush();
    }

    private static void WriteLocalHeader(Stream stream, byte[] nameBytes, uint crc, int size)
    {
        WriteUInt32(stream, LocalFileHeaderSignature);
        WriteUInt16(stream, VersionNeededDefault);
        WriteUInt16(stream, Utf8Flag);
        WriteUInt16(stream, StoredMethod);
        WriteUInt16(stream, 0);
        WriteUInt16(stream, 0);
        WriteUInt32(stream, crc);
        WriteUInt32(stream, (uint)size);
        WriteUInt32(stream, (uint)size);
        WriteUInt16(stream, (ushort)nameBytes.Length);
        WriteUInt16(stream, 0);
        stream.Write(nameBytes);
    }

    private static void WriteCentralHeader(Stream stream, byte[] nameBytes, uint crc, int size, long localOffset)
    {
        var needsZip64Offset = localOffset >= uint.MaxValue;
        WriteUInt32(stream, CentralDirectorySignature);
        WriteUInt16(stream, VersionNeededZip64);
        WriteUInt16(stream, needsZip64Offset ? VersionNeededZip64 : VersionNeededDefault);
        WriteUInt16(stream, Utf8Flag);
        WriteUInt16(stream, StoredMethod);
        WriteUInt16(stream, 0);
        WriteUInt16(stream, 0);
        WriteUInt32(stream, crc);
        WriteUInt32(stream, (uint)size);
        WriteUInt32(stream, (uint)size);
        WriteUInt16(stream, (ushort)nameBytes.Length);
        WriteUInt16(stream, (ushort)(needsZip64Offset ? 12 : 0));
        WriteUInt16(stream, 0);
        WriteUInt16(stream, 0);
        WriteUInt16(stream, 0);
        WriteUInt32(stream, 0);
        WriteUInt32(stream, needsZip64Offset ? uint.MaxValue : (uint)localOffset);
        stream.Write(nameBytes);
        if (needsZip64Offset)
        {
            WriteUInt16(stream, Zip64ExtraId);
            WriteUInt16(stream, 8);
            WriteUInt64(stream, (ulong)localOffset);
        }
    }

    private static void WriteZip64EndRecords(Stream stream, long entries, long centralSize, long centralOffset)
    {
        var zip64EndOffset = stream.Position;
        WriteUInt32(stream, Zip64EndOfCentralDirectorySignature);
        WriteUInt64(stream, 44);
        WriteUInt16(stream, VersionNeededZip64);
        WriteUInt16(stream, VersionNeededZip64);
        WriteUInt32(stream, 0);
        WriteUInt32(stream, 0);
        WriteUInt64(stream, (ulong)entries);
        WriteUInt64(stream, (ulong)entries);
        WriteUInt64(stream, (ulong)centralSize);
        WriteUInt64(stream, (ulong)centralOffset);

        WriteUInt32(stream, Zip64EndOfCentralDirectoryLocatorSignature);
        WriteUInt32(stream, 0);
        WriteUInt64(stream, (ulong)zip64EndOffset);
        WriteUInt32(stream, 1);
    }

    private static void WriteEndRecord(Stream stream, long entries, long centralSize, long centralOffset, bool zip64)
    {
        WriteUInt32(stream, EndOfCentralDirectorySignature);
        WriteUInt16(stream, 0);
        WriteUInt16(stream, 0);
        WriteUInt16(stream, zip64 ? ushort.MaxValue : (ushort)entries);
        WriteUInt16(stream, zip64 ? ushort.MaxValue : (ushort)entries);
        WriteUInt32(stream, zip64 ? uint.MaxValue : (uint)centralSize);
        WriteUInt32(stream, zip64 ? uint.MaxValue : (uint)centralOffset);
        WriteUInt16(stream, 0);
    }

    private static void WriteUInt16(Stream stream, ushort value)
    {
        Span<byte> buffer = stackalloc byte[sizeof(ushort)];
        BinaryPrimitives.WriteUInt16LittleEndian(buffer, value);
        stream.Write(buffer);
    }

    private static void WriteUInt32(Stream stream, uint value)
    {
        Span<byte> buffer = stackalloc byte[sizeof(uint)];
        BinaryPrimitives.WriteUInt32LittleEndian(buffer, value);
        stream.Write(buffer);
    }

    private static void WriteUInt64(Stream stream, ulong value)
    {
        Span<byte> buffer = stackalloc byte[sizeof(ulong)];
        BinaryPrimitives.WriteUInt64LittleEndian(buffer, value);
        stream.Write(buffer);
    }
}

internal static class Crc32
{
    private static readonly uint[] Table = BuildTable();

    public static uint Compute(ReadOnlySpan<byte> data)
    {
        var crc = 0xFFFF_FFFFu;
        foreach (var value in data)
            crc = (crc >> 8) ^ Table[(crc ^ value) & 0xFF];
        return ~crc;
    }

    private static uint[] BuildTable()
    {
        var table = new uint[256];
        for (uint i = 0; i < table.Length; i++)
        {
            var crc = i;
            for (var bit = 0; bit < 8; bit++)
                crc = (crc & 1) != 0 ? 0xEDB8_8320u ^ (crc >> 1) : crc >> 1;
            table[i] = crc;
        }
        return table;
    }
}

public sealed record SplitterConfig
{
    public string InputDirectory { get; init; } = Path.GetFullPath("Phase3SpindaBlocks");
    public string InputPattern { get; init; } = "*.spinda80.zip";
    public IReadOnlyList<string> InputZips { get; init; } = [];
    public string SaveDirectory { get; init; } = Path.GetFullPath("TSVs");
    public string ShinyOutputPath { get; init; } = Path.GetFullPath(Path.Combine("HatchedSpindaZips", "spinda-hatched-shiny.zip"));
    public string NotShinyOutputPath { get; init; } = Path.GetFullPath(Path.Combine("HatchedSpindaZips", "spinda-hatched-not-shiny.zip"));
    public string? ReportPath { get; init; }
    public string? TrainerIndexOutputPath { get; init; }
    public int TrainerId { get; init; } = 0;
    public int ExpectedSpecies { get; init; } = (int)Species.Spinda;
    public int HatchLevel { get; init; } = 5;
    public int SampleLimit { get; init; } = 64;
    public long? LimitEntries { get; init; }
    public bool AllowAlreadyHatched { get; init; }
    public bool AllowPartialSaveBank { get; init; }
    public bool Compress { get; init; }
    public bool IncludeManifest { get; init; } = true;
    public bool Overwrite { get; init; }
    public bool ShowHelp { get; init; }
    public bool SkipBadRecords { get; init; }
    public bool TrustSaveFilenames { get; init; }

    public static SplitterConfig Parse(string[] args)
    {
        var inputDirectory = Path.GetFullPath("Phase3SpindaBlocks");
        var inputPattern = "*.spinda80.zip";
        var inputZips = new List<string>();
        var saveDirectory = Path.GetFullPath("TSVs");
        var shinyOutput = Path.GetFullPath(Path.Combine("HatchedSpindaZips", "spinda-hatched-shiny.zip"));
        var notShinyOutput = Path.GetFullPath(Path.Combine("HatchedSpindaZips", "spinda-hatched-not-shiny.zip"));
        string? report = null;
        string? trainerIndexOutput = null;
        var trainerId = 0;
        var expectedSpecies = (int)Species.Spinda;
        var hatchLevel = 5;
        var sampleLimit = 64;
        long? limitEntries = null;
        var allowAlreadyHatched = false;
        var allowPartialSaveBank = false;
        var compress = false;
        var includeManifest = true;
        var overwrite = false;
        var showHelp = false;
        var skipBadRecords = false;
        var trustSaveFilenames = false;

        for (var i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--input-dir":
                    inputDirectory = RequireValue(args, ref i);
                    break;
                case "--input-pattern":
                    inputPattern = RequireValue(args, ref i);
                    break;
                case "--input-zip":
                    inputZips.Add(RequireValue(args, ref i));
                    break;
                case "--save-dir":
                    saveDirectory = RequireValue(args, ref i);
                    break;
                case "--shiny-output":
                    shinyOutput = RequireValue(args, ref i);
                    break;
                case "--not-shiny-output":
                    notShinyOutput = RequireValue(args, ref i);
                    break;
                case "--report":
                    report = RequireValue(args, ref i);
                    break;
                case "--trainer-index":
                    trainerIndexOutput = RequireValue(args, ref i);
                    break;
                case "--trainer-id":
                    trainerId = int.Parse(RequireValue(args, ref i));
                    break;
                case "--expected-species":
                    expectedSpecies = int.Parse(RequireValue(args, ref i));
                    break;
                case "--hatch-level":
                    hatchLevel = int.Parse(RequireValue(args, ref i));
                    break;
                case "--sample-limit":
                    sampleLimit = int.Parse(RequireValue(args, ref i));
                    break;
                case "--limit-entries":
                    limitEntries = long.Parse(RequireValue(args, ref i));
                    break;
                case "--allow-already-hatched":
                    allowAlreadyHatched = true;
                    break;
                case "--allow-partial-save-bank":
                    allowPartialSaveBank = true;
                    break;
                case "--compress":
                    compress = true;
                    break;
                case "--no-manifest":
                    includeManifest = false;
                    break;
                case "--overwrite":
                    overwrite = true;
                    break;
                case "--skip-bad-records":
                    skipBadRecords = true;
                    break;
                case "--trust-save-filenames":
                    trustSaveFilenames = true;
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

        return new SplitterConfig
        {
            InputDirectory = inputDirectory,
            InputPattern = inputPattern,
            InputZips = inputZips,
            SaveDirectory = saveDirectory,
            ShinyOutputPath = shinyOutput,
            NotShinyOutputPath = notShinyOutput,
            ReportPath = report,
            TrainerIndexOutputPath = trainerIndexOutput,
            TrainerId = trainerId,
            ExpectedSpecies = expectedSpecies,
            HatchLevel = hatchLevel,
            SampleLimit = sampleLimit,
            LimitEntries = limitEntries,
            AllowAlreadyHatched = allowAlreadyHatched,
            AllowPartialSaveBank = allowPartialSaveBank,
            Compress = compress,
            IncludeManifest = includeManifest,
            Overwrite = overwrite,
            ShowHelp = showHelp,
            SkipBadRecords = skipBadRecords,
            TrustSaveFilenames = trustSaveFilenames,
        };
    }

    public void Validate()
    {
        if (TrainerId is < 0 or > ushort.MaxValue)
            throw new ArgumentOutOfRangeException(nameof(TrainerId), "--trainer-id must be 0..65535.");
        if (ExpectedSpecies is < 1 or > ushort.MaxValue)
            throw new ArgumentOutOfRangeException(nameof(ExpectedSpecies), "--expected-species must be a valid ushort species id.");
        if (HatchLevel is < 1 or > 100)
            throw new ArgumentOutOfRangeException(nameof(HatchLevel), "--hatch-level must be 1..100.");
        if (SampleLimit < 0)
            throw new ArgumentOutOfRangeException(nameof(SampleLimit), "--sample-limit must be non-negative.");
        if (LimitEntries is < 1)
            throw new ArgumentOutOfRangeException(nameof(LimitEntries), "--limit-entries must be positive.");
        if (string.IsNullOrWhiteSpace(SaveDirectory))
            throw new ArgumentException("--save-dir is required.");
        if (!Directory.Exists(SaveDirectory))
            throw new DirectoryNotFoundException($"Save directory does not exist: {SaveDirectory}");
    }

    public static void PrintUsage()
    {
        Console.WriteLine("""
        SpindaHatchZipSplitter

        Standalone PKHeX.Core hatch converter for Phase 3 Spinda egg ZIPs.
        It streams .pk3 egg records from input ZIPs and writes two output ZIPs:
        one hatched shiny under the matching TSV save and one hatched not-shiny
        under a non-matching TSV save. Production defaults expect Trainer ID 0.

        Options:
          --input-dir PATH              Folder scanned for input ZIPs. Default: .\Phase3SpindaBlocks
          --input-pattern GLOB          Input ZIP glob in --input-dir. Default: *.spinda80.zip
          --input-zip PATH              Add one explicit input ZIP. Can repeat.
          --save-dir PATH               Folder with TSV-xxxx-sid-xxxxx.sav files. Default: .\TSVs
          --shiny-output PATH           Output ZIP for shiny hatched copies.
          --not-shiny-output PATH       Output ZIP for not-shiny hatched copies.
          --report PATH                 JSON report path.
          --trainer-index PATH          Write a TSV trainer index JSON and exit.
          --trainer-id N                Expected TID. Default: 0.
          --expected-species N          Expected species. Default: 327 (Spinda).
          --hatch-level N               Level applied before hatching. Default: 5.
          --limit-entries N             Proof-run limit.
          --sample-limit N              Samples stored in report. Default: 64.
          --allow-partial-save-bank     Do not require all 8192 TSV saves.
          --allow-already-hatched       Permit source records that are not eggs.
          --compress                    Use Fastest compression. Default is no compression for speed.
          --no-manifest                 Do not embed _spinda_hatch_manifest.json.
          --overwrite                   Replace existing output ZIPs.
          --skip-bad-records            Emit partial outputs instead of failing on bad records.
          --trust-save-filenames        Use TSV/SID from filenames without parsing saves.
        """);
    }

    private static string RequireValue(string[] args, ref int index)
    {
        if (index + 1 >= args.Length)
            throw new ArgumentException($"Missing value for {args[index]}");
        return args[++index];
    }
}

public sealed class TsvSaveIndex
{
    // TSV is a fixed 0..8191 domain, so array lookup is cheaper than hashing
    // for every Spinda in the hot loop.
    private readonly TsvSaveContext?[] byTsv;
    private readonly List<TsvSaveContext> ordered;

    private TsvSaveIndex(TsvSaveContext?[] byTsv, List<TsvSaveContext> ordered)
    {
        this.byTsv = byTsv;
        this.ordered = ordered.OrderBy(static context => context.Tsv).ToList();
    }

    public int Count => ordered.Count;
    public IReadOnlyList<TsvSaveContext> Contexts => ordered;

    public bool TryGet(int tsv, out TsvSaveContext context)
    {
        if ((uint)tsv >= HatchZipSplitter.ShinyValueCount)
        {
            context = null!;
            return false;
        }

        context = byTsv[tsv]!;
        return context is not null;
    }

    public bool TryGetNonMatching(int psv, out TsvSaveContext context)
    {
        var preferred = (psv + 1) & (HatchZipSplitter.ShinyValueCount - 1);
        context = byTsv[preferred]!;
        if (context is not null)
            return true;
        context = ordered.FirstOrDefault(candidate => candidate.Tsv != psv)!;
        return context is not null;
    }

    public static TsvSaveIndex Load(SplitterConfig config, SplitResult result)
    {
        var contexts = new TsvSaveContext?[HatchZipSplitter.ShinyValueCount];
        var ordered = new List<TsvSaveContext>(HatchZipSplitter.ShinyValueCount);
        var savePaths = Directory.EnumerateFiles(config.SaveDirectory, "*.sav", SearchOption.TopDirectoryOnly)
            .OrderBy(static path => Path.GetFileName(path), StringComparer.OrdinalIgnoreCase);

        foreach (var path in savePaths)
        {
            var name = Path.GetFileName(path);
            var match = HatchZipSplitter.SaveNamePattern.Match(name);
            if (!match.Success)
                continue;

            var filenameTsv = int.Parse(match.Groups["tsv"].Value);
            var filenameSid = int.Parse(match.Groups["sid"].Value);
            if (filenameTsv is < 0 or >= HatchZipSplitter.ShinyValueCount || filenameSid is < 0 or > ushort.MaxValue)
            {
                result.AddIssue("save_filename_range", name, hard: true);
                continue;
            }

            var computedTsv = (config.TrainerId ^ filenameSid) >> 3;
            if (computedTsv != filenameTsv)
            {
                result.AddIssue("save_filename_tsv_mismatch", $"{name}:computed={computedTsv:D4}", hard: true);
                continue;
            }

            if (contexts[filenameTsv] is not null)
            {
                result.AddIssue("duplicate_tsv_save", name, hard: true);
                continue;
            }

            if (!TryBuildContext(path, filenameTsv, filenameSid, config, out var context, out var issue))
            {
                result.AddIssue("save_parse_failed", $"{name}:{issue}", hard: true);
                continue;
            }
            contexts[filenameTsv] = context;
            ordered.Add(context);
        }

        if (!config.AllowPartialSaveBank && ordered.Count != HatchZipSplitter.ShinyValueCount)
            result.AddIssue("save_bank_incomplete", $"loaded={ordered.Count}:expected={HatchZipSplitter.ShinyValueCount}", hard: true);

        return new TsvSaveIndex(contexts, ordered);
    }

    private static bool TryBuildContext(
        string path,
        int filenameTsv,
        int filenameSid,
        SplitterConfig config,
        out TsvSaveContext context,
        out string issue)
    {
        if (config.TrustSaveFilenames)
        {
            context = new TsvSaveContext(path, filenameTsv, config.TrainerId, filenameSid, "Spinda", 0, (int)LanguageID.English, GameVersion.LG);
            issue = "";
            return true;
        }

        try
        {
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            var data = new byte[stream.Length];
            var offset = 0;
            while (offset < data.Length)
            {
                var read = stream.Read(data, offset, data.Length - offset);
                if (read == 0)
                    break;
                offset += read;
            }
            if (offset != data.Length)
            {
                context = default!;
                issue = "short_read";
                return false;
            }

            var save = SaveUtil.GetSaveFile(data.AsMemory(), path);
            if (save is null)
            {
                context = default!;
                issue = "pkhex_null_save";
                return false;
            }
            ParseSettings.InitFromSaveFileData(save);
            ParseSettings.AllowEraCartGBA = true;

            if (save.TID16 != config.TrainerId)
            {
                context = default!;
                issue = $"tid={save.TID16}:expected={config.TrainerId}";
                return false;
            }
            if (save.SID16 != filenameSid)
            {
                context = default!;
                issue = $"sid={save.SID16}:filename={filenameSid}";
                return false;
            }
            context = new TsvSaveContext(
                path,
                filenameTsv,
                save.TID16,
                save.SID16,
                save.OT,
                save.Gender,
                NormalizePk3Language(save.Language),
                save.Version);
            issue = "";
            return true;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or ArgumentException or InvalidOperationException)
        {
            context = default!;
            issue = $"{ex.GetType().Name}:{ex.Message}";
            return false;
        }
    }

    private static int NormalizePk3Language(int language) =>
        language == (int)LanguageID.None ? (int)LanguageID.English : language;
}

public sealed record TsvSaveContext(
    string SavePath,
    int Tsv,
    int TrainerId,
    int SecretId,
    string TrainerName,
    byte Gender,
    int Language,
    GameVersion Version)
{
    public SimpleTrainerInfo ToTrainerInfo() => new(Version)
    {
        OT = string.IsNullOrWhiteSpace(TrainerName) ? "Spinda" : TrainerName,
        TID16 = (ushort)TrainerId,
        SID16 = (ushort)SecretId,
        Gender = Gender,
        Language = Language,
    };
}

public sealed record TrainerIndexWriteResult(
    string OutputPath,
    int EntryCount,
    bool Complete,
    long HardIssueCount,
    long SoftIssueCount);

public sealed class TrainerIndexDocument
{
    public string Format { get; init; } = "spinda-tsv-trainer-index-v1";
    public DateTimeOffset GeneratedUtc { get; init; }
    public string SaveDirectory { get; init; } = "";
    public int ExpectedEntries { get; init; }
    public int EntryCount { get; init; }
    public bool Complete { get; init; }
    public int TrainerId { get; init; }
    public long HardIssueCount { get; init; }
    public long SoftIssueCount { get; init; }
    public Dictionary<string, long> IssueCounts { get; init; } = new(StringComparer.Ordinal);
    public List<ConversionIssue> Issues { get; init; } = [];
    public List<TrainerIndexEntry> Entries { get; init; } = [];
}

public sealed class TrainerIndexEntry
{
    public int Tsv { get; init; }
    public string TsvHex { get; init; } = "";
    public int TrainerId { get; init; }
    public string TrainerIdHex { get; init; } = "";
    public int SecretId { get; init; }
    public string SecretIdHex { get; init; } = "";
    public string TrainerName { get; init; } = "";
    public byte Gender { get; init; }
    public int Language { get; init; }
    public string Version { get; init; } = "";
    public string SavePath { get; init; } = "";
    public string SaveFileName { get; init; } = "";
    public string SaveSha1 { get; init; } = "";
    public int ComputedTsv { get; init; }

    public static TrainerIndexEntry FromContext(TsvSaveContext context)
    {
        var fullPath = Path.GetFullPath(context.SavePath);
        return new TrainerIndexEntry
        {
            Tsv = context.Tsv,
            TsvHex = $"0x{context.Tsv:X4}",
            TrainerId = context.TrainerId,
            TrainerIdHex = $"0x{context.TrainerId:X4}",
            SecretId = context.SecretId,
            SecretIdHex = $"0x{context.SecretId:X4}",
            TrainerName = context.TrainerName,
            Gender = context.Gender,
            Language = context.Language,
            Version = context.Version.ToString(),
            SavePath = fullPath,
            SaveFileName = Path.GetFileName(fullPath),
            SaveSha1 = ComputeSha1(fullPath),
            ComputedTsv = (context.TrainerId ^ context.SecretId) >> 3,
        };
    }

    private static string ComputeSha1(string path)
    {
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete, 1 << 20);
        return Convert.ToHexString(SHA1.HashData(stream));
    }
}

public sealed class SplitResult
{
    public DateTimeOffset StartedUtc { get; init; }
    public DateTimeOffset FinishedUtc { get; set; }
    public double ElapsedSeconds { get; set; }
    public string SaveDirectory { get; init; } = "";
    public string InputDirectory { get; init; } = "";
    public List<string> InputZips { get; } = [];
    public int InputZipCount { get; set; }
    public int SourceZipsVisited { get; set; }
    public string? ShinyOutputPath { get; set; }
    public string? NotShinyOutputPath { get; set; }
    public string? ReportPath { get; set; }
    public int SaveContextsLoaded { get; set; }
    public bool SaveBankComplete { get; set; }
    public long ProcessedEntries { get; set; }
    public long ShinyWritten { get; set; }
    public long NotShinyWritten { get; set; }
    public long ForcedDecryptCount { get; set; }
    public long[] PsvCounts { get; } = new long[8192];
    public long[] ShinySaveUseCounts { get; } = new long[8192];
    public long[] NonShinySaveUseCounts { get; } = new long[8192];
    public Dictionary<string, long> IssueCounts { get; } = new(StringComparer.Ordinal);
    public List<ConversionIssue> Issues { get; } = [];
    public List<ConversionSample> Samples { get; } = [];
    public int SampleLimit { get; init; } = 64;
    public long HardIssueCount { get; private set; }
    public long SoftIssueCount { get; private set; }
    public long TotalIssueCount => HardIssueCount + SoftIssueCount;
    public bool ShouldAddSample => Samples.Count < SampleLimit;

    public void AddIssue(string code, string detail, bool hard)
    {
        IssueCounts[code] = IssueCounts.GetValueOrDefault(code) + 1;
        if (hard)
            HardIssueCount++;
        else
            SoftIssueCount++;

        // Keep the report bounded while counters keep full corpus-scale totals.
        if (Issues.Count < 512)
            Issues.Add(new ConversionIssue(code, detail, hard));
    }

    public void AddSample(ConversionSample sample)
    {
        if (Samples.Count < SampleLimit)
            Samples.Add(sample);
    }
}

internal readonly record struct EntrySource(string ZipFileName, string EntryName)
{
    public override string ToString() => string.Concat(ZipFileName, ":", EntryName);
}

public sealed record ConversionIssue(string Code, string Detail, bool Hard);

public sealed record ConversionSample
{
    public required string Source { get; init; }
    public required string EntryName { get; init; }
    public required string Pid { get; init; }
    public int Psv { get; init; }
    public int ShinyTsv { get; init; }
    public int ShinySid { get; init; }
    public int NonShinyTsv { get; init; }
    public int NonShinySid { get; init; }
}
