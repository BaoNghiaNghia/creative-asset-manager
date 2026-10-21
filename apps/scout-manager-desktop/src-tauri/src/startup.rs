//! Explicit local process startup, parsed before touching pairing or profiles.
use std::ffi::OsStr;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum StartupAction {
    Idle,
    StartAutomation,
}

pub(crate) fn parse_args<I, S>(args: I) -> Result<StartupAction, &'static str>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    let mut args = args.into_iter();
    match (args.next(), args.next()) {
        (None, None) => Ok(StartupAction::Idle),
        (Some(arg), None) if arg.as_ref() == OsStr::new("--start-automation") => {
            Ok(StartupAction::StartAutomation)
        }
        _ => Err("Usage: Scout Manager [--start-automation]. Unsupported startup arguments."),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn starts_idle_without_explicit_flag() {
        assert_eq!(parse_args(Vec::<String>::new()), Ok(StartupAction::Idle));
    }

    #[test]
    fn accepts_only_one_exact_start_flag() {
        assert_eq!(
            parse_args(["--start-automation"]),
            Ok(StartupAction::StartAutomation)
        );
        for args in [
            vec!["--START-AUTOMATION"],
            vec!["--start-automation=true"],
            vec!["--start-automation", "--start-automation"],
            vec!["--start-automation", ""],
            vec!["--start-automation", "extra"],
            vec!["--"],
            vec![""],
            vec!["operator-secret-must-not-be-printed"],
        ] {
            let error = parse_args(args).unwrap_err();
            assert_eq!(
                error,
                "Usage: Scout Manager [--start-automation]. Unsupported startup arguments."
            );
            assert!(!error.contains("operator-secret"));
        }
    }

    #[cfg(windows)]
    #[test]
    fn rejects_non_unicode_argument_without_reflecting_it() {
        use std::ffi::OsString;
        use std::os::windows::ffi::OsStringExt;
        assert!(parse_args([OsString::from_wide(&[0xd800])]).is_err());
    }
}
