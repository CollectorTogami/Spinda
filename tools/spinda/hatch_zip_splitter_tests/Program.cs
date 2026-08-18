using System.IO.Compression;
using PKHeX.Core;
using SpindaHatchZipSplitter;

internal static class Program
{
    private static int Main()
    {
        var tempRoot = Path.Combine(Path.GetTempPath(), "spinda-hatch-zip-splitter-tests", Guid.NewGuid().ToString("N"));
        try
        {
            Directory.CreateDirectory(tempRoot);
            TestSplitsSyntheticEggIntoShinyAndNotShiny(tempRoot);
            TestMissingMatchingTsvFailsBeforeOutput(tempRoot);
            TestHardIssueBeyondIssueSampleCapStillBlocksOutput(tempRoot);
            Console.WriteLine("SpindaHatchZipSplitter tests passed.");
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex);
            return 1;
        }
        finally
        {
            if (Directory.Exists(tempRoot))
                Directory.Delete(tempRoot, recursive: true);
        }
    }

    private static void TestSplitsSyntheticEggIntoShinyAndNotShiny(string root)
    {
        var caseRoot = Path.Combine(root, "split");
        var inputDir = Path.Combine(caseRoot, "input");
        var saveDir = Path.Combine(caseRoot, "TSVs");
        var outputDir = Path.Combine(caseRoot, "out");
        Directory.CreateDirectory(inputDir);
        Directory.CreateDirectory(saveDir);
        Directory.CreateDirectory(outputDir);

        const uint pid = 0x1234ABCD;
        var psv = HatchZipSplitter.ComputePokemonShinyValue(pid);
        CreatePlaceholderSave(saveDir, psv, psv << 3);
        CreatePlaceholderSave(saveDir, (psv + 1) & 8191, ((psv + 1) & 8191) << 3);

        var inputZip = Path.Combine(inputDir, "0xABCD.spinda80.zip");
        CreateEggZip(inputZip, pid);

        var shinyOutput = Path.Combine(outputDir, "shiny.zip");
        var notShinyOutput = Path.Combine(outputDir, "not-shiny.zip");
        var result = HatchZipSplitter.Run(new SplitterConfig
        {
            InputDirectory = inputDir,
            SaveDirectory = saveDir,
            ShinyOutputPath = shinyOutput,
            NotShinyOutputPath = notShinyOutput,
            AllowPartialSaveBank = true,
            TrustSaveFilenames = true,
            Overwrite = true,
        });

        AssertEqual(0L, result.HardIssueCount, "hard issue count");
        AssertEqual(1L, result.ProcessedEntries, "processed entry count");
        AssertTrue(File.Exists(shinyOutput), "shiny output exists");
        AssertTrue(File.Exists(notShinyOutput), "not-shiny output exists");

        var shiny = ReadOnlyPk3(shinyOutput, $"0x{pid:X8}.pk3");
        var notShiny = ReadOnlyPk3(notShinyOutput, $"0x{pid:X8}.pk3");

        AssertFalse(shiny.IsEgg, "shiny copy hatched");
        AssertFalse(notShiny.IsEgg, "not-shiny copy hatched");
        AssertEqual((int)Species.Spinda, shiny.Species, "shiny species");
        AssertEqual((int)Species.Spinda, notShiny.Species, "not-shiny species");
        AssertEqual(0, shiny.TID16, "shiny TID");
        AssertEqual(0, notShiny.TID16, "not-shiny TID");
        AssertEqual(psv << 3, shiny.SID16, "shiny SID");
        AssertEqual(((psv + 1) & 8191) << 3, notShiny.SID16, "not-shiny SID");
        AssertTrue(shiny.IsShiny, "shiny copy shiny");
        AssertFalse(notShiny.IsShiny, "not-shiny copy shiny flag");
        AssertTrue(shiny.ChecksumValid, "shiny checksum");
        AssertTrue(notShiny.ChecksumValid, "not-shiny checksum");
    }

    private static void TestMissingMatchingTsvFailsBeforeOutput(string root)
    {
        var caseRoot = Path.Combine(root, "missing-tsv");
        var inputDir = Path.Combine(caseRoot, "input");
        var saveDir = Path.Combine(caseRoot, "TSVs");
        var outputDir = Path.Combine(caseRoot, "out");
        Directory.CreateDirectory(inputDir);
        Directory.CreateDirectory(saveDir);
        Directory.CreateDirectory(outputDir);

        const uint pid = 0x4321AAAA;
        var psv = HatchZipSplitter.ComputePokemonShinyValue(pid);
        CreatePlaceholderSave(saveDir, (psv + 1) & 8191, ((psv + 1) & 8191) << 3);
        CreateEggZip(Path.Combine(inputDir, "0xAAAA.spinda80.zip"), pid);

        var shinyOutput = Path.Combine(outputDir, "shiny.zip");
        var notShinyOutput = Path.Combine(outputDir, "not-shiny.zip");
        var result = HatchZipSplitter.Run(new SplitterConfig
        {
            InputDirectory = inputDir,
            SaveDirectory = saveDir,
            ShinyOutputPath = shinyOutput,
            NotShinyOutputPath = notShinyOutput,
            AllowPartialSaveBank = true,
            TrustSaveFilenames = true,
            Overwrite = true,
        });

        AssertTrue(result.HardIssueCount > 0, "missing TSV is hard issue");
        AssertFalse(File.Exists(shinyOutput), "shiny output not moved into place");
        AssertFalse(File.Exists(notShinyOutput), "not-shiny output not moved into place");
    }

    private static void TestHardIssueBeyondIssueSampleCapStillBlocksOutput(string root)
    {
        var caseRoot = Path.Combine(root, "issue-cap");
        var inputDir = Path.Combine(caseRoot, "input");
        var saveDir = Path.Combine(caseRoot, "TSVs");
        var outputDir = Path.Combine(caseRoot, "out");
        Directory.CreateDirectory(inputDir);
        Directory.CreateDirectory(saveDir);
        Directory.CreateDirectory(outputDir);

        const uint pid = 0x22334455;
        var psv = HatchZipSplitter.ComputePokemonShinyValue(pid);
        CreatePlaceholderSave(saveDir, psv, psv << 3);
        CreatePlaceholderSave(saveDir, (psv + 1) & 8191, ((psv + 1) & 8191) << 3);

        var inputZip = Path.Combine(inputDir, "0x4455.spinda80.zip");
        using (var archive = ZipFile.Open(inputZip, ZipArchiveMode.Create))
        {
            var egg = CreateStoredEgg(pid);
            for (var i = 0; i < 512; i++)
                WriteEntry(archive, $"soft-name-{i:D4}.pk3", egg);
            WriteEntry(archive, "hard-failure.txt", [1, 2, 3, 4]);
        }

        var shinyOutput = Path.Combine(outputDir, "shiny.zip");
        var notShinyOutput = Path.Combine(outputDir, "not-shiny.zip");
        var result = HatchZipSplitter.Run(new SplitterConfig
        {
            InputDirectory = inputDir,
            SaveDirectory = saveDir,
            ShinyOutputPath = shinyOutput,
            NotShinyOutputPath = notShinyOutput,
            AllowPartialSaveBank = true,
            TrustSaveFilenames = true,
            Overwrite = true,
        });

        AssertEqual(512L, result.ProcessedEntries, "soft entries processed before hard failure");
        AssertEqual(1L, result.HardIssueCount, "hard issue count beyond sampled issue list");
        AssertEqual(512L, result.SoftIssueCount, "soft issue count");
        AssertEqual(513L, result.TotalIssueCount, "total issue count");
        AssertEqual(512L, result.IssueCounts["entry_name_not_pid"], "entry-name issue counter");
        AssertEqual(1L, result.IssueCounts["bad_entry_extension"], "bad extension counter");
        AssertEqual(512, result.Issues.Count, "sampled issue cap");
        AssertTrue(result.Issues.All(static issue => !issue.Hard), "sampled issues are soft only");
        AssertFalse(File.Exists(shinyOutput), "shiny output not moved into place after unsampled hard issue");
        AssertFalse(File.Exists(notShinyOutput), "not-shiny output not moved into place after unsampled hard issue");
    }

    private static void CreatePlaceholderSave(string saveDir, int tsv, int sid)
    {
        File.WriteAllText(Path.Combine(saveDir, $"TSV-{tsv:D4}-sid-{sid:D5}.sav"), "synthetic");
    }

    private static void CreateEggZip(string zipPath, uint pid)
    {
        using var archive = ZipFile.Open(zipPath, ZipArchiveMode.Create);
        WriteEntry(archive, $"0x{pid:X8}.pk3", CreateStoredEgg(pid));
    }

    private static void WriteEntry(ZipArchive archive, string entryName, ReadOnlySpan<byte> data)
    {
        var entry = archive.CreateEntry(entryName, CompressionLevel.NoCompression);
        using var stream = entry.Open();
        stream.Write(data);
    }

    private static byte[] CreateStoredEgg(uint pid)
    {
        var pk = new PK3
        {
            PID = pid,
            Species = (int)Species.Spinda,
            TID16 = 12345,
            SID16 = 54321,
            Language = (int)LanguageID.English,
            OriginalTrainerName = "EGG",
            Version = GameVersion.LG,
            Ball = 4,
            MetLocation = Locations.HatchLocationFRLG,
            MetLevel = 0,
            CurrentLevel = 5,
            Move1 = (ushort)Move.Tackle,
            OriginalTrainerFriendship = 1,
            IV_HP = 1,
            IV_ATK = 2,
            IV_DEF = 3,
            IV_SPE = 4,
            IV_SPA = 5,
            IV_SPD = 6,
        };
        pk.IsEgg = true;
        pk.RefreshChecksum();
        var bytes = new byte[pk.SIZE_STORED];
        pk.WriteEncryptedDataStored(bytes);
        return bytes;
    }

    private static PK3 ReadOnlyPk3(string zipPath, string entryName)
    {
        using var archive = ZipFile.OpenRead(zipPath);
        var entry = archive.GetEntry(entryName) ?? throw new InvalidOperationException($"Missing ZIP entry {entryName}");
        using var stream = entry.Open();
        var bytes = new byte[80];
        var offset = 0;
        while (offset < bytes.Length)
        {
            var read = stream.Read(bytes, offset, bytes.Length - offset);
            if (read == 0)
                throw new InvalidOperationException($"Short ZIP entry {entryName}");
            offset += read;
        }
        return new PK3(bytes);
    }

    private static void AssertTrue(bool condition, string message)
    {
        if (!condition)
            throw new InvalidOperationException($"Assertion failed: {message}");
    }

    private static void AssertFalse(bool condition, string message) => AssertTrue(!condition, message);

    private static void AssertEqual<T>(T expected, T actual, string message)
        where T : IEquatable<T>
    {
        if (!expected.Equals(actual))
            throw new InvalidOperationException($"Assertion failed: {message}; expected {expected}, got {actual}");
    }
}
