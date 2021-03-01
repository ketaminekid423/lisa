# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the Apache License.

# Get initial variable list
$initialGlobalVarList = Get-Variable -Scope "Global" | Select-Object -ExpandProperty Name

Function Import-LibrariesModules {
	param (
		[string] $WorkingDirectory
	)
	$libraryModuleNames = @()
	Get-ChildItem (Join-Path $WorkingDirectory "Libraries") -Recurse | Where-Object { $_.FullName.EndsWith(".psm1") } | `
		ForEach-Object {$libraryModuleNames += $_.BaseName; Import-Module $_.FullName -Force -Global -DisableNameChecking -Verbose:$false}
	return $libraryModuleNames
}

function Start-LISAv2 {
	[CmdletBinding()]
	Param(
		[string] $ParametersFile = "",

		# [Required]
		[string] $TestPlatform = "",

		# [Required] for Azure.
		[string] $TestLocation="",
		[string] $ARMImageName = "",
		[string] $StorageAccount="",

		# [Required] for Two Hosts HyperV
		[string] $DestinationOsVHDPath="",

		# [Required] Common for HyperV and Azure.
		[string] $RGIdentifier = "",
		[string] $OsVHD = "",   #... [Azure: Required only if -ARMImageName is not provided.]
								#... [HyperV: Mandatory]
								#... [WSL: Mandatory, which can be the URL of the distro, or the path to the distro file on the local host]
								#... [Ready: Not needed, and will be ignored if provided]
		[string] $TestCategory = "",
		[string] $TestArea = "",
		[string] $TestTag = "",
		[string] $TestNames="",
		[string] $TestPriority="",
		[string] $TestSetup="",

		# [Optional] Exclude the tests from being executed. (Comma separated values)
		[string] $ExcludeTests = "",

		# [Optional] Enable kernel code coverage
		[switch] $EnableCodeCoverage,

		# [Optional] Parameters for Image preparation before running tests.
		[string] $CustomKernel = "",
		[string] $CustomLIS,

		# [Optional] Parameters for changing framework behavior.
		[int]    $TestIterations = 1,
		[string] $XMLSecretFile = "",
		[switch] $EnableTelemetry,
		[switch] $UseExistingRG,

		# [Optional] Parameters for setting TiPCluster=ClusterId;TipSessionId=SessionId;DiskType=Managed/Unmanaged;Networking=SRIOV/Synthetic.
		[string] $CustomParameters = "",

		# [Optional] Parameters for Overriding VM Configuration.
		[string] $CustomTestParameters = "",
		[string] $OverrideVMSize = "",
		[ValidateSet('Default','Keep','Delete',IgnoreCase = $true)]
		[string] $ResourceCleanup,
		[switch] $DeployVMPerEachTest,
		[string] $VMGeneration = "",

		[string] $ResultDBTable = "",
		[string] $ResultDBTestTag = "",
		[string] $TestPassID = "",

		[switch] $ExitWithZero,
		[switch] $ForceCustom,
		[switch] $ReuseVmOnFailure,
		[switch] $RunInParallel,
		[int]    $TotalCountInParallel,
		[object] $ParamsInParallel,
		[string] $TestIdInParallel,
		[int]    $ParallelTimeoutHours
	)

	PROCESS {
		try {
			Import-LibrariesModules -WorkingDirectory $PSScriptRoot | Out-Null
			# Legacy code of Set-Variable -Global $WorkingDirectory
			$workingDirectory = $PSScriptRoot
			Set-Variable -Name "WorkingDirectory" -Value $workingDirectory -Scope Global

			# Prepare $TestId before all other logic, and after importing Library module TestLogs.psm1 (Write-LogInfo)
			if ($TestIdInParallel) {
				$testId = $TestIdInParallel
				$envTempPath = "$WorkingDirectory\Temp\$TestIdInParallel"
			}
			else {
				$testId = New-TestId
				Write-LogInfo "Autogenerated test ID: $testId"
				$envTempPath = "$WorkingDirectory\Temp"
			}
			Set-Variable -Name "TestId" -Value $testId -Scope Global -Force

			# Modify environment TEMP variable after Set-Variable $WorkingDirectory
			if (!(test-path $envTempPath)) {
				New-Item -Path $envTempPath -ItemType Directory -Force | Out-Null
			}
			$env:TEMP = $envTempPath

			# Prepare $LogDir and $LogFileName after Set-Variable $WorkingDirectory
			$testTimestamp = Get-Date -Format 'yyyy-MM-dd-HH-mm-ss'
			$logDir = Join-Path $workingDirectory "TestResults\${testTimestamp}-$global:TestId"
			New-Item -ItemType "Directory" -Path $logDir -Force | Out-Null
			Set-Variable -Name "LogDir" -Value $logDir -Scope Global -Force
			$logFileName = "LISAv2-Test-${testId}.log"
			Set-Variable -Name "LogFileName" -Value $logFileName -Scope Global -Force
			Write-LogInfo "Logging path: $(Join-Path $LogDir $LogFileName)"

			# Import parameters from file if ParametersFile parameter is given
			# and set them as global variables
			$paramTable = @{}
			if ($ParametersFile) {
				$paramTable = Import-TestParameters -ParametersFile $ParametersFile
			}
			# Processing parameters provided in the command runtime
			$paramList = (Get-Command -Name $PSCmdlet.MyInvocation.InvocationName).Parameters
			foreach ($paramName in $paramList.Keys) {
				$paramValue = (Get-Variable -Name $paramName -ErrorAction "SilentlyContinue").Value
				if ($paramValue) {
					if ($paramTable.ContainsKey($paramName)) {
						Write-LogWarn "Duplicated parameter '$paramName' and overwrite with value: $($paramTable[$paramName]) -> $paramValue"
						$paramTable[$paramName] = $paramValue
					} else {
						Write-LogInfo "Setting parameter: $paramName = $paramValue"
						$paramTable.Add($paramName, $paramValue)
					}
				}
			}

			# Validate test platform, and select test controller of the platform
			if ($paramTable.ContainsKey("TestPlatform")) {
				$testPlatform = $paramTable["TestPlatform"]
			}
			if ($testPlatform) {
				$moduleName = "TestControllers\$($testPlatform)Controller.psm1"
				if ([System.IO.File]::Exists("$PSScriptRoot\$moduleName")) {
					. $([ScriptBlock]::Create("using module $moduleName"))
					$testController = New-Object -TypeName $testPlatform"Controller"
				} else {
					throw "$testPlatform is not yet supported."
				}
			} else {
				throw "'TestPlatform' parameter is not provided."
			}

			# Validate the test parameters.
			$testController.ParseAndValidateParameters($paramTable)

			# Handle the secrets file
			if ($env:Azure_Secrets_File) {
				$XMLSecretFile = $env:Azure_Secrets_File
				Write-LogInfo "The Secrets file is defined by an environment variable."
			}
			$testController.PrepareTestEnvironment($XMLSecretFile)

			# Validate all the XML files and then import test cases from them for test
			Validate-XmlFiles -ParentFolder (Join-Path $workingDirectory "XML")

			if ($paramTable.ContainsKey("CustomTestParameters")) {
				$CustomTestParameters = $paramTable["CustomTestParameters"]
			}
			$testController.LoadTestCases($workingDirectory, $CustomTestParameters)

			# Create report folder
			$reportFolder = Join-Path $workingDirectory "Report"
			if (!(Test-Path $reportFolder)) {
				New-Item -ItemType "Directory" $reportFolder | Out-Null
			}
			$TestReportXml = Join-Path "$reportFolder" "LISAv2_TestReport_$testId-junit.xml"

			# Create result folder
			$TestResultsDir = "$WorkingDirectory\TestResults"
			if (!(Test-Path $TestResultsDir)) {
				New-Item -ItemType "Directory" $TestResultsDir | Out-Null
			}

			# Run test
			$testController.RunLoadedTestCases($TestReportXml, $TestIterations, $RunInParallel.IsPresent)
			Write-LogInfo "Test $global:testId finished"

			# Output text summary
			$plainTextSummary = $testController.TestSummary.GetPlainTextSummary($OsVHD, $testController.ARMImageName, $OverrideVMSize)
			Write-LogInfo $plainTextSummary

			# Zip the test log folder
			$zipFile = "$TestPlatform"
			if ( $TestCategory ) { $zipFile += "-$TestCategory"	}
			if ( $TestArea ) { $zipFile += "-$TestArea" }
			if ( $TestTag ) { $zipFile += "-$($TestTag)" }
			if ( $TestPriority ) { $zipFile += "-$($TestPriority)" }
			$zipFile += "-$testId-TestLogs.zip"
			$zipFile = $zipFile.Replace("*", "All")
			$zipFilePath = Join-Path (Get-Location).Path $zipFile
			New-ZipFile -zipFileName $zipFilePath -sourceDir $LogDir

			# for parallel run, only calculate the error counts in main process
			if ($RunInParallel) {
				$reportFiles = Get-ChildItem "$reportFolder" | Where-Object { $_.FullName -imatch "LISAv2_TestReport_${TestId}([\-\d])+junit.xml" }
			# non-parallel run, and not the sub-process of parallel run
			} elseif (-not $TestIdInParallel) {
				$reportFiles = @(Get-Item -path $TestReportXml)
			}

			$ExitCode = 0
			$failedCount = 0
			$errorCount = 0
			$testCount = 0
			foreach ($reportFile in $reportFiles) {
				$reportFilePath = $reportFile.FullName
				Write-LogInfo "Analyzing test results from $reportFilePath ..."
				if (Test-Path -Path $reportFilePath) {
					try {
						$results = [xml](Get-Content $reportFilePath -ErrorAction SilentlyContinue)
					} catch {
						throw "Could not parse test results from the test report."
					}
					$testSuiteresults = $results.testsuites.testsuite

					$failedCount += [int] ($testSuiteresults.failures)
					$errorCount += [int] ($testSuiteresults.errors)
					$testCount += [int] ($testSuiteresults.tests)
				} else {
					Write-LogErr "Summary file: $reportFilePath does not exist. Exiting with error code 1."
					$ExitCode = 1
					return
				}
			}

			if (($failedCount -eq 0 -and $errorCount -eq 0 -and $testCount -gt 0) -or (-not $RunInParallel -and $TestIdInParallel)) {
				$ExitCode = 0
			} else {
				$ExitCode = 1
			}
		} catch {
			$line = $_.InvocationInfo.ScriptLineNumber
			$script_name = ($_.InvocationInfo.ScriptName).Replace($PWD,".")
			$ErrorMessage =  $_.Exception.Message

			Write-LogErr "EXCEPTION: $ErrorMessage"
			Write-LogErr "Source: Line $line in script $script_name."
			$ExitCode = 1
		} finally {
			if ( $ExitWithZero -and ($ExitCode -ne 0) ) {
				Write-LogInfo "Forcefully exiting with exit code 0 as ExitWithZero flag was set to $true"
				$ExitCode = 0
			}
			Write-LogInfo "LISAv2 exit code: $ExitCode"
			if ($OriginalWorkingDirectory) {
				Move-BackToOriginalWorkingSpace $WorkingDirectory $OriginalWorkingDirectory $ExitCode
			}
			# Remove all variables that cannot be found in the initial list
			$finalGlobalVarList = Get-Variable -Exclude ("ExitCode", "LASTEXITCODE") -Scope "Global" | Select-Object -ExpandProperty Name
			$removableGlobalVarList = (Compare-Object $initialGlobalVarList $finalGlobalVarList).InputObject

			Get-Variable -Include $removableGlobalVarList -Scope "Global" | Remove-Variable -Scope "Global" `
				-Force -ErrorAction SilentlyContinue

			if ($ExitCode -ne 0) {
				throw "LISAv2 failed with exit code: $ExitCode"
			}
		}
	}
}

New-Alias -Name Run-LISAv2 -Value Start-LISAv2 -Force

Export-ModuleMember -Function * -Alias *
