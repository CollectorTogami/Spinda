using System.Text.Json;

namespace SpindaHatchZipSplitter;

internal static class Program
{
    public static int Main(string[] args)
    {
        try
        {
            var config = SplitterConfig.Parse(args);
            if (config.ShowHelp)
            {
                SplitterConfig.PrintUsage();
                return 0;
            }

            if (!string.IsNullOrWhiteSpace(config.TrainerIndexOutputPath))
            {
                var indexResult = HatchZipSplitter.WriteTrainerIndex(config);
                Console.WriteLine(
                    $"trainer_index_entries={indexResult.EntryCount} complete={indexResult.Complete} issues={indexResult.HardIssueCount}");
                Console.WriteLine($"trainer_index={indexResult.OutputPath}");
                return indexResult.HardIssueCount == 0 ? 0 : 1;
            }

            var result = HatchZipSplitter.Run(config);
            Console.WriteLine(
                $"inputs={result.InputZipCount} processed={result.ProcessedEntries} " +
                $"shiny={result.ShinyWritten} not_shiny={result.NotShinyWritten} issues={result.HardIssueCount}");
            if (!string.IsNullOrWhiteSpace(result.ReportPath))
                Console.WriteLine($"report={result.ReportPath}");
            if (!string.IsNullOrWhiteSpace(result.ShinyOutputPath))
                Console.WriteLine($"shiny_output={result.ShinyOutputPath}");
            if (!string.IsNullOrWhiteSpace(result.NotShinyOutputPath))
                Console.WriteLine($"not_shiny_output={result.NotShinyOutputPath}");

            return result.HardIssueCount == 0 ? 0 : 1;
        }
        catch (JsonException ex)
        {
            Console.Error.WriteLine($"JSON error: {ex.Message}");
            return 2;
        }
        catch (Exception ex) when (ex is ArgumentException or ArgumentOutOfRangeException or IOException or UnauthorizedAccessException or InvalidDataException)
        {
            Console.Error.WriteLine($"{ex.GetType().Name}: {ex.Message}");
            return 2;
        }
    }
}
